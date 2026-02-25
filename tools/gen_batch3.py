#!/usr/bin/env python3
"""Batch 3: Agents 7-10"""
import yaml
from pathlib import Path
BASE = Path(__file__).parent.parent / "agents"

def w(d, core, sources):
    p = BASE / d; p.mkdir(parents=True, exist_ok=True)
    with open(p/"core.yaml","w",encoding="utf-8") as f: yaml.dump(core,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    with open(p/"sources.yaml","w",encoding="utf-8") as f: yaml.dump(sources,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    print(f"  ✅ {d}: {len(core.get('eval_questions',[]))} q")

# ═══ 7: FENOLOGI ═══
w("fenologi",{
    "header":{"agent_id":"fenologi","agent_name":"Fenologi","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Huhdasjärvi/Kouvola, vyöhyke II-III","Fenologiset havainnot kytketty mehiläishoitoon ja puutarhaan","Ilmatieteen laitoksen termisen kasvukauden määritelmä: vrk-keskilämpö >5°C vähintään 5 vrk"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "thermal_growing_season_start":{"value":"Vrk-keskilämpö pysyvästi >5°C, tyypillisesti vko 16-18 (Kouvola)","action":"Käynnistä kevään hoitosuunnitelma mehiläisillä ja puutarhassa","source":"src:FEN1"},
        "effective_temperature_sum_deg_days":{"value":"Seuraa °Cvr kertymää päivittäin (base 5°C)","thresholds":{"pajun_kukinta":"50-80 °Cvr","voikukan_kukinta":"150-200 °Cvr","omenapuun_kukinta":"300-350 °Cvr","maitohorsman_kukinta":"800-1000 °Cvr","varroa_hoito_viimeistaan":"1200 °Cvr (≈ sadonkorjuun jälkeen)"},"source":"src:FEN1"},
        "autumn_colour_trigger":{"value":"Vrk-keskilämpö pysyvästi <10°C → lehtien värimuutos alkaa","source":"src:FEN1"},
        "first_frost_typical_date":{"value":"Syyskuun loppu – lokakuun alku (Kouvola)","action":"Herkät kasvit suojaan, ilmoita hortonomille","source":"src:FEN1"},
        "snow_cover_permanent":{"value":"Tyypillisesti marraskuun loppu – joulukuun alku","source":"src:FEN1"},
        "ice_formation_lake":{"value":"Jääpeite tyypillisesti marraskuun puoliväli (Huhdasjärvi)","action":"Ilmoita jääasiantuntijalle","source":"src:FEN1"}
    },
    "KNOWLEDGE_TABLES":{
        "phenological_calendar":[
            {"event":"Pajun kukinta","typical_week":"vko 16-18","deg_days":"50-80 °Cvr","bee_relevance":"Ensimmäinen merkittävä mesilähde","source":"src:FEN1"},
            {"event":"Voikukka kukkii","typical_week":"vko 19-21","deg_days":"150-200 °Cvr","bee_relevance":"Siitepölyä runsaasti","source":"src:FEN1"},
            {"event":"Tuomi kukkii","typical_week":"vko 21-23","deg_days":"200-250 °Cvr","bee_relevance":"Perinteinen 'takatalvi' indikaattori","source":"src:FEN2"},
            {"event":"Omenapuu kukkii","typical_week":"vko 22-24","deg_days":"300-350 °Cvr","bee_relevance":"Pölytystehon mittari","source":"src:FEN1"},
            {"event":"Maitohorsma kukkii","typical_week":"vko 27-33","deg_days":"800-1000 °Cvr","bee_relevance":"Pääsatokasvi","source":"src:FEN1"},
            {"event":"Ensimmäinen halla","typical_week":"vko 38-41","deg_days":"N/A","bee_relevance":"Kasvukauden päättyminen, talviruokinnan suunnittelu","source":"src:FEN1"}
        ]
    },
    "PROCESS_FLOWS":{
        "daily_monitoring":{"steps":["1. Lue vrk-keskilämpö (meteorologi-agentilta)","2. Laske °Cvr-kertymä (∑max(0, T_avg - 5))","3. Vertaa kynnysarvoihin → laukaise ilmoitukset","4. Päivitä fenologinen kalenteri havainnoilla","5. Ilmoita relevanteille agenteille (tarhaaja, hortonomi, lentosää)"],"source":"src:FEN1"}
    },
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"°Cvr-seuranta alkaa kun vrk-keskilämpö >5°C. Pajun kukinta → ilmoita tarhaajalle kevätruokinnan lopetuksesta.","source":"src:FEN1"},
        {"season":"Kesä","action":"Seuraa maitohorsman kukinta-ajan alkua (800 °Cvr) → ilmoita nektari-informaatikolle. Seuraa hellekausia (>25°C).","source":"src:FEN1"},
        {"season":"Syksy","action":"Ensimmäinen halla → ilmoita hortonomille ja tarhaajalle. Pysyvä <5°C → kasvukausi päättyy.","source":"src:FEN1"},
        {"season":"Talvi","action":"Jääpeite-seuranta, lumensyvyys, pakkasvuorokausien kertymä routaseurantaa varten.","source":"src:FEN1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Poikkeuksellisen aikainen/myöhäinen kevät","detection":"°Cvr-kertymä >20% eri tasolla kuin 10v keskiarvo samaan aikaan","action":"Varoita kaikkia biologisia agentteja poikkeavasta ajoituksesta","source":"src:FEN1"},
        {"mode":"Hallayö kasvukaudella","detection":"Ennuste <0°C touko-syyskuussa","action":"HÄLYTYS hortonomille, tarhaajalle; pakkassuojaus","source":"src:FEN1"}
    ],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["°Cvr-kynnysarvot ovat tyypillisiä Kaakkois-Suomelle — vuosivaihtelu ±15%.","Fenologiset havainnot ovat paikallisia; järvien läheisyys vaikuttaa mikroilmastoon."],
    "eval_questions":[
        {"q":"Mikä on termisen kasvukauden alkuehto?","a_ref":"ASSUMPTIONS","source":"src:FEN1"},
        {"q":"Milloin pajun kukinta tyypillisesti alkaa?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[0].typical_week","source":"src:FEN1"},
        {"q":"Mikä on pajun kukinnan °Cvr-kynnys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.pajun_kukinta","source":"src:FEN1"},
        {"q":"Mikä on maitohorsman °Cvr-kynnys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.maitohorsman_kukinta","source":"src:FEN1"},
        {"q":"Milloin varroa-hoito tulisi viimeistään tehdä (°Cvr)?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.varroa_hoito_viimeistaan","source":"src:FEN1"},
        {"q":"Mikä on tyypillinen ensimmäisen hallan ajankohta?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.first_frost_typical_date.value","source":"src:FEN1"},
        {"q":"Milloin omenapuu tyypillisesti kukkii?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[3].typical_week","source":"src:FEN1"},
        {"q":"Miten °Cvr lasketaan?","a_ref":"PROCESS_FLOWS.daily_monitoring.steps","source":"src:FEN1"},
        {"q":"Mikä laukaisee syksyn värimuutoksen?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.autumn_colour_trigger.value","source":"src:FEN1"},
        {"q":"Milloin jääpeite muodostuu Huhdasjärvelle?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.ice_formation_lake.value","source":"src:FEN1"},
        {"q":"Mikä on voikukan °Cvr-kynnys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.voikukan_kukinta","source":"src:FEN1"},
        {"q":"Kenelle pajun kukinnasta ilmoitetaan?","a_ref":"SEASONAL_RULES[0].action","source":"src:FEN1"},
        {"q":"Mikä on tuomen kukinnalle tyypillinen ilmiö?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[2].bee_relevance","source":"src:FEN2"},
        {"q":"Milloin lumipeite vakiintuu?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.snow_cover_permanent.value","source":"src:FEN1"},
        {"q":"Mitä tapahtuu poikkeuksellisen aikaisessa keväässä?","a_ref":"FAILURE_MODES[0].action","source":"src:FEN1"},
        {"q":"Kenelle hallasta ilmoitetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:FEN1"},
        {"q":"Mikä on omenapuun °Cvr-kynnys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.omenapuun_kukinta","source":"src:FEN1"},
        {"q":"Miten kasvukausi päättyy fenologisesti?","a_ref":"SEASONAL_RULES[2].action","source":"src:FEN1"},
        {"q":"Miten poikkeavuus havaitaan °Cvr:ssä?","a_ref":"FAILURE_MODES[0].detection","source":"src:FEN1"},
        {"q":"Milloin voikukka kukkii?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[1].typical_week","source":"src:FEN1"},
        {"q":"Mikä on kevään kasvukauden tyypillinen aloitusviikko Kouvolassa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.thermal_growing_season_start.value","source":"src:FEN1"},
        {"q":"Mitä seurataan talvella?","a_ref":"SEASONAL_RULES[3].action","source":"src:FEN1"},
        {"q":"Onko °Cvr-vuosivaihtelu merkittävää?","a_ref":"UNCERTAINTY_NOTES","source":"src:FEN1"},
        {"q":"Mikä on maitohorsman merkitys mehiläisille?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[4].bee_relevance","source":"src:FEN1"},
        {"q":"Kenelle nektaritiedosta ilmoitetaan kesällä?","a_ref":"SEASONAL_RULES[1].action","source":"src:FEN1"},
        {"q":"Miten hellekautta seurataan?","a_ref":"SEASONAL_RULES[1].action","source":"src:FEN1"},
        {"q":"Milloin tuomi kukkii?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[2].typical_week","source":"src:FEN1"},
        {"q":"Vaikuttaako järvi paikalliseen fenologiaan?","a_ref":"UNCERTAINTY_NOTES","source":"src:FEN1"},
        {"q":"Mikä on ensimmäisen hallan merkitys mehiläisille?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[5].bee_relevance","source":"src:FEN1"},
        {"q":"Milloin maitohorsma tyypillisesti kukkii?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[4].typical_week","source":"src:FEN1"},
        {"q":"Mikä on pölytystehon mittari keväällä?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[3].bee_relevance","source":"src:FEN1"},
        {"q":"Miten routaseurantaa varten tietoa kerätään?","a_ref":"SEASONAL_RULES[3].action","source":"src:FEN1"},
        {"q":"Mikä on voikukan merkitys mehiläisille?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[1].bee_relevance","source":"src:FEN1"},
        {"q":"Mikä on päivittäisen fenologisen seurannan ensimmäinen askel?","a_ref":"PROCESS_FLOWS.daily_monitoring.steps","source":"src:FEN1"},
        {"q":"Mihin ensimmäisen hallan havainnot johtavat?","a_ref":"SEASONAL_RULES[2].action","source":"src:FEN1"},
        {"q":"Mikä on kasvukauden päättymisen fenologinen merkki?","a_ref":"SEASONAL_RULES[2].action","source":"src:FEN1"},
        {"q":"Milloin tuomen kukinta tapahtuu °Cvr:nä?","a_ref":"KNOWLEDGE_TABLES.phenological_calendar[2].deg_days","source":"src:FEN1"},
        {"q":"Kenelle jääpeite-havainto ilmoitetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.ice_formation_lake.action","source":"src:FEN1"},
        {"q":"Miten fenologia liittyy mehiläishoitoon?","a_ref":"ASSUMPTIONS","source":"src:FEN1"},
        {"q":"Mikä on base-lämpötila °Cvr-laskennassa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.value","source":"src:FEN1"},
    ]
},{"sources":[
    {"id":"src:FEN1","org":"Ilmatieteen laitos","title":"Kasvukauden tilastot ja terminen kasvukausi","year":2024,"url":"https://www.ilmatieteenlaitos.fi/kasvukauden-olosuhteet","supports":"Kasvukauden alku/loppu, °Cvr, fenologiset kynnykset."},
    {"id":"src:FEN2","org":"Luonnontieteellinen keskusmuseo (Luomus)","title":"Fenologinen seuranta","year":2024,"url":"https://www.luomus.fi/fi/fenologia","supports":"Valtakunnallinen fenologinen seuranta, kukintojen ajoitukset."}
]})

# ═══ 8: PIENELÄIN- JA TUHOLAISASIANTUNTIJA ═══
w("pienelain_tuholais",{
    "header":{"agent_id":"pienelain_tuholais","agent_name":"Pieneläin- ja tuholaisasiantuntija","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Korvenrannan mökkiympäristö — metsä ja järvi","Jyrsijät, kärpäset, hyttyset, punkit, muurahaiset, ampiaisen","Torjunta ensisijaisesti mekaanisin ja biologisin keinoin"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "mouse_activity_threshold":{"value":"Jätöksiä >10 kpl / m² tai purusahanjauhoa rakenteissa","action":"Aseta loukkuja, tarkista rakenteiden tiiveys, ilmoita timpurille","source":"src:PIE1"},
        "tick_risk_level":{"value":"Aktiivinen kun vrk-keskilämpö >5°C (huhti–marras)","action":"Punkkitarkistus ulkoilun jälkeen, repellenttiä","source":"src:PIE2"},
        "wasp_nest_proximity_m":{"value":5,"action":"Ampiaispesä <5m oleskelualueesta → poisto tai merkintä","source":"src:PIE1"},
        "mosquito_peak_conditions":{"value":"Iltalämpö >15°C + kosteus >70% + tuuleton → huippu","source":"src:PIE1"},
        "rat_sign_threshold":{"value":"Yksikin rotanjätös sisätiloissa → välitön toimenpide","action":"Ammattilainen paikalle, elintarvikkeet turvaan","source":"src:PIE1"}
    },
    "KNOWLEDGE_TABLES":{
        "common_pests":[
            {"pest":"Metsämyyrä (Myodes glareolus)","risk":"Puunkuoren kalvaminen talvella, jäljet puutarhassa","control":"Suojukset taimiin, loukkupyynti","source":"src:PIE1"},
            {"pest":"Kotihiiri (Mus musculus)","risk":"Elintarvikkeet, johdot, eristeet","control":"Loukkupyynti, tiivistäminen","source":"src:PIE1"},
            {"pest":"Puutiainen (Ixodes ricinus)","risk":"Borrelioosi, TBE","control":"Repellentti, pukeutuminen, tarkistus","source":"src:PIE2"},
            {"pest":"Yleinen ampiainen (Vespula vulgaris)","risk":"Pistot, allergiariski","control":"Pesän poisto (ammattilainen), syöttiloukut","source":"src:PIE1"},
            {"pest":"Muurahainen (Camponotus spp.)","risk":"Puurakenteissa → rakennevauriot","control":"Paikanna pesä, boorihapon syötti","source":"src:PIE1"}
        ]
    },
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Hiirten talvivaurioiden tarkistus puutarhassa. Punkkikausi alkaa. Muurahaiskartoitus rakennusten seinustoilla.","source":"src:PIE1"},
        {"season":"Kesä","action":"Hyttystorjunta (seisova vesi pois), ampiaispesien kartoitus heinäkuusta, kärpästen torjunta (kompostialueet).","source":"src:PIE1"},
        {"season":"Syksy","action":"Hiirten sisääntunkeutumisen esto — tiivistä aukot <6mm. Rotat etsivät suojaa. Punkkikausi jatkuu lokakuuhun.","source":"src:PIE1"},
        {"season":"Talvi","action":"Myyrien lumireiät ja jäljet seurannassa. Loukut tarkistetaan 2x/vko. Hiiret aktiivisia sisätiloissa.","source":"src:PIE1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Rottahavainto","detection":"Jätökset (12-18mm, tummat) tai kaivautumisreiät perustuksissa","action":"Ammattimainen rotantorjunta VÄLITTÖMÄSTI, ilmoita terveystarkastajalle tarvittaessa","source":"src:PIE1"},
        {"mode":"Ampiaispesä seinärakenteessa","detection":"Ampiaiset lentävät rakenteen sisään/ulos","action":"ÄLÄ tuki reikää → ampiaiset tulevat sisäkautta. Kutsu tuholaistorjuja.","source":"src:PIE1"}
    ],
    "COMPLIANCE_AND_LEGAL":{
        "rodenticides":"Jyrsijämyrkkyjen ammattimainen käyttö vaatii tuholaistorjuja-tutkinnon (Tukes) [src:PIE3]",
        "protected_species":"Liito-orava, lepakot — suojeltuja, pesäpaikkoja ei saa tuhota [src:PIE4]"
    },
    "UNCERTAINTY_NOTES":["Punkin levittämien tautien esiintyvyys vaihtelee alueittain — TBE-riski Kaakkois-Suomessa matala mutta kasvava.","Myyräsyklin huippuvuosien ennustaminen on epätarkkaa."],
    "eval_questions":[
        {"q":"Mikä on hiirihavainnon kynnys toimenpiteelle?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.mouse_activity_threshold.value","source":"src:PIE1"},
        {"q":"Milloin punkkikausi alkaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.tick_risk_level.value","source":"src:PIE2"},
        {"q":"Mikä on ampiaispesän vähimmäisetäisyys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.wasp_nest_proximity_m.value","source":"src:PIE1"},
        {"q":"Milloin hyttyset ovat aktiivisimmillaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.mosquito_peak_conditions.value","source":"src:PIE1"},
        {"q":"Mitä tehdään rottahavainnossa?","a_ref":"FAILURE_MODES[0].action","source":"src:PIE1"},
        {"q":"Mikä on rotan jätöksen koko?","a_ref":"FAILURE_MODES[0].detection","source":"src:PIE1"},
        {"q":"Saako ampiaispesän reiän tukkia?","a_ref":"FAILURE_MODES[1].action","source":"src:PIE1"},
        {"q":"Vaatiiko jyrsijämyrkyn käyttö tutkinnon?","a_ref":"COMPLIANCE_AND_LEGAL.rodenticides","source":"src:PIE3"},
        {"q":"Onko liito-orava suojeltu?","a_ref":"COMPLIANCE_AND_LEGAL.protected_species","source":"src:PIE4"},
        {"q":"Mikä aukon koko estää hiiren sisäänpääsyn?","a_ref":"SEASONAL_RULES[2].action","source":"src:PIE1"},
        {"q":"Miten metsämyyrä tunnistetaan?","a_ref":"KNOWLEDGE_TABLES.common_pests[0].risk","source":"src:PIE1"},
        {"q":"Mikä on puutiaisen terveysriski?","a_ref":"KNOWLEDGE_TABLES.common_pests[2].risk","source":"src:PIE2"},
        {"q":"Miten muurahaispesä puurakenteessa havaitaan?","a_ref":"KNOWLEDGE_TABLES.common_pests[4].risk","source":"src:PIE1"},
        {"q":"Kuinka usein loukkuja tarkistetaan talvella?","a_ref":"SEASONAL_RULES[3].action","source":"src:PIE1"},
        {"q":"Mistä seisova vesi poistetaan kesällä?","a_ref":"SEASONAL_RULES[1].action","source":"src:PIE1"},
        {"q":"Milloin ampiaispesien kartoitus tehdään?","a_ref":"SEASONAL_RULES[1].action","source":"src:PIE1"},
        {"q":"Mikä torjuntakeino muurahaisille?","a_ref":"KNOWLEDGE_TABLES.common_pests[4].control","source":"src:PIE1"},
        {"q":"Mitä hiiri tuhoaa sisätiloissa?","a_ref":"KNOWLEDGE_TABLES.common_pests[1].risk","source":"src:PIE1"},
        {"q":"Mikä on TBE-riski Kaakkois-Suomessa?","a_ref":"UNCERTAINTY_NOTES","source":"src:PIE2"},
        {"q":"Milloin metsämyyrän tuhoja tarkistetaan?","a_ref":"SEASONAL_RULES[0].action","source":"src:PIE1"},
        {"q":"Mikä on ampiaisen riski?","a_ref":"KNOWLEDGE_TABLES.common_pests[3].risk","source":"src:PIE1"},
        {"q":"Miten ratanjätös eroaa hiiren jätöksestä?","a_ref":"FAILURE_MODES[0].detection","source":"src:PIE1"},
        {"q":"Kenelle hiirituhoista ilmoitetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.mouse_activity_threshold.action","source":"src:PIE1"},
        {"q":"Onko myyräsykli ennustettavissa?","a_ref":"UNCERTAINTY_NOTES","source":"src:PIE1"},
        {"q":"Milloin punkkikausi päättyy?","a_ref":"SEASONAL_RULES[2].action","source":"src:PIE1"},
        {"q":"Miten hiiren sisäänpääsy estetään?","a_ref":"SEASONAL_RULES[2].action","source":"src:PIE1"},
        {"q":"Mikä on hiiriloukkupyynnin ensisijainen keino?","a_ref":"KNOWLEDGE_TABLES.common_pests[1].control","source":"src:PIE1"},
        {"q":"Onko lepakko suojeltu?","a_ref":"COMPLIANCE_AND_LEGAL.protected_species","source":"src:PIE4"},
        {"q":"Mikä on ampiaisen torjuntakeino?","a_ref":"KNOWLEDGE_TABLES.common_pests[3].control","source":"src:PIE1"},
        {"q":"Mitä keväällä tarkistetaan muurahaisista?","a_ref":"SEASONAL_RULES[0].action","source":"src:PIE1"},
        {"q":"Milloin rotat etsivät suojaa?","a_ref":"SEASONAL_RULES[2].action","source":"src:PIE1"},
        {"q":"Mikä on punkin torjuntakeino?","a_ref":"KNOWLEDGE_TABLES.common_pests[2].control","source":"src:PIE2"},
        {"q":"Mitä puutiainen levittää?","a_ref":"KNOWLEDGE_TABLES.common_pests[2].risk","source":"src:PIE2"},
        {"q":"Kenelle rotasta ilmoitetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:PIE1"},
        {"q":"Miten kompostialueen kärpäset torjutaan?","a_ref":"SEASONAL_RULES[1].action","source":"src:PIE1"},
        {"q":"Mikä on myyrien torjuntakeino talvella?","a_ref":"SEASONAL_RULES[3].action","source":"src:PIE1"},
        {"q":"Voiko metsämyyrä vahingoittaa puutarhaa?","a_ref":"KNOWLEDGE_TABLES.common_pests[0].risk","source":"src:PIE1"},
        {"q":"Kenelle ampiaispesästä seinärakenteessa ilmoitetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:PIE1"},
        {"q":"Mikä on ensisijainen torjuntaperiaate?","a_ref":"ASSUMPTIONS","source":"src:PIE1"},
        {"q":"Mikä on rottahavainnon prioriteetti?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.rat_sign_threshold.action","source":"src:PIE1"},
    ]
},{"sources":[
    {"id":"src:PIE1","org":"Tukes","title":"Tuholaistorjunta","year":2025,"url":"https://tukes.fi/kemikaalit/biosidivalmisteet/tuholaistorjunta","supports":"Jyrsijätorjunta, ampiaistorjunta, muurahaiset."},
    {"id":"src:PIE2","org":"THL","title":"Puutiaisten levittämät taudit","year":2025,"url":"https://thl.fi/fi/web/infektiotaudit-ja-rokotukset/taudit-ja-torjunta/taudit-ja-taudinaiheuttajat-a-o/puutiaisaivotulehdus","supports":"Borrelioosi, TBE, punkkien aktiivisuuskaudet."},
    {"id":"src:PIE3","org":"Tukes","title":"Tuholaistorjujatutkinto","year":2025,"url":"https://tukes.fi/kemikaalit/biosidivalmisteet/tuholaistorjuja","supports":"Ammattimaisen torjunnan pätevyysvaatimukset."},
    {"id":"src:PIE4","org":"Oikeusministeriö","title":"Luonnonsuojelulaki 9/2023","year":2023,"url":"https://www.finlex.fi/fi/laki/ajantasa/2023/20230009","supports":"Suojellut eläinlajit, pesäpaikkojen turvaaminen."}
]})

# ═══ 9: ENTOMOLOGI (Hyönteistutkija) ═══
w("entomologi",{
    "header":{"agent_id":"entomologi","agent_name":"Entomologi (Hyönteistutkija)","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Fokus: mehiläisiin vaikuttavat hyönteiset + kasvintuholaiset + metsätuholaiset","Korvenrannan lähiympäristö, vyöhyke II-III","Kytkentä tarhaaja-, metsänhoitaja- ja hortonomi-agentteihin"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "varroa_mite_threshold_per_100_bees":{"value":3,"action":">3 punkkia / 100 mehiläistä → välitön kemiallinen hoito","source":"src:ENT1"},
        "bark_beetle_trap_threshold":{"value":"Feromonipyydyksessä >500 kirjanpainajaa / 2 viikkoa → hakkuuhälytys","source":"src:ENT2"},
        "pollinator_diversity_index":{"value":"Shannon H' >2.0 normaali, <1.5 hälytys","action":"Matala diversiteetti → tarkista torjunta-aineet ja elinympäristö","source":"src:ENT1"},
        "aphid_colony_density":{"value":">50 kirvaa / verso → biologinen torjunta (leppäpirkot)","source":"src:ENT3"},
        "wax_moth_detection":{"value":"Toukkien seittiverkkoa kehyksillä → puhdista ja pakasta kehykset -18°C 48h","source":"src:ENT1"}
    },
    "KNOWLEDGE_TABLES":{
        "key_insects":[
            {"insect":"Varroapunkki (Varroa destructor)","role":"Mehiläisparasiitti","severity":"KRIITTINEN","monitoring":"Pudotusalustamittaus, sokerijauhomenetelmä","source":"src:ENT1"},
            {"insect":"Kirjanpainaja (Ips typographus)","role":"Kuusen tuholainen","severity":"KORKEA","monitoring":"Feromonipyydykset touko-elokuussa","source":"src:ENT2"},
            {"insect":"Leppäpirkko (Coccinellidae)","role":"Kirvansyöjä (biologinen torjunta)","severity":"HYÖDYLLINEN","monitoring":"Populaatioseuranta puutarhassa","source":"src:ENT3"},
            {"insect":"Vahakoi (Galleria mellonella)","role":"Mehiläispesän tuholainen","severity":"KESKITASO","monitoring":"Varastoitujen kehysten tarkistus","source":"src:ENT1"},
            {"insect":"Pieni pesäkuoriainen (Aethina tumida)","role":"Mehiläisparasiitti (EI vielä Suomessa)","severity":"POTENTIAALINEN","monitoring":"EU-tarkkailu, ilmoita havainnoista Ruokavirastolle","source":"src:ENT4"}
        ]
    },
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Varroa-pudotusalustan asennus huhti-toukokuussa. Kirjanpainaja-pyydykset paikoilleen toukokuussa.","source":"src:ENT1"},
        {"season":"Kesä","action":"Kirvapopulaation seuranta, leppäpirkkojen suojelu (EI laaja-alaista torjuntaruiskutusta). Varroa-luontainen pudotus seurannassa.","source":"src:ENT1"},
        {"season":"Syksy","action":"Varroa-hoito oksaalihapolla (pesimättömänä aikana). Vahakoi-kehysten pakastus ennen varastointia.","source":"src:ENT1"},
        {"season":"Talvi","action":"Kehysvarastojen seuranta (vahakoi, hiiret). Varroahoitotuloksen arviointi keväällä.","source":"src:ENT1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Varroa-hoito epäonnistunut","detection":"Pudotusmäärä edelleen >3/100 hoidon jälkeen","action":"Toinen hoitokierros eri valmisteella. Ilmoita tautivahti-agentille.","source":"src:ENT1"},
        {"mode":"Kirjanpainajaepidemia","detection":">500 yksilöä pyydyksessä tai useita kuivuvia kuusia","action":"Välitön puunkaato. Ilmoita metsänhoitajalle. Kaarnan poltto.","source":"src:ENT2"}
    ],
    "COMPLIANCE_AND_LEGAL":{
        "varroa_treatment":"Lääkinnälliset varroa-valmisteet vain eläinlääkkeinä hyväksytyt (Ruokavirasto) [src:ENT4]",
        "pesticide_reporting":"Kasvinsuojeluainerekisteri (Tukes): vain hyväksytyt valmisteet [src:ENT3]"
    },
    "UNCERTAINTY_NOTES":["Pieni pesäkuoriainen voi levitä Suomeen ilmastonmuutoksen myötä — seuranta tärkeä.","Varroa-resistenssi hoitoaineille kasvava ongelma Euroopassa."],
    "eval_questions":[
        {"q":"Mikä on varroa-kynnys hoitotoimenpiteelle?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.varroa_mite_threshold_per_100_bees.value","source":"src:ENT1"},
        {"q":"Mikä on kirjanpainajapyydyksen hälytysraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.bark_beetle_trap_threshold.value","source":"src:ENT2"},
        {"q":"Mikä on hyvä pölyttäjädiversiteetti (Shannon H')?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.pollinator_diversity_index.value","source":"src:ENT1"},
        {"q":"Mikä on kirvatiheyden hälytysraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.aphid_colony_density.value","source":"src:ENT3"},
        {"q":"Miten vahakoi havaitaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.wax_moth_detection.value","source":"src:ENT1"},
        {"q":"Miten varroa-pudotusta seurataan?","a_ref":"KNOWLEDGE_TABLES.key_insects[0].monitoring","source":"src:ENT1"},
        {"q":"Onko pieni pesäkuoriainen Suomessa?","a_ref":"KNOWLEDGE_TABLES.key_insects[4].role","source":"src:ENT4"},
        {"q":"Milloin varroa-hoito oksaalihapolla tehdään?","a_ref":"SEASONAL_RULES[2].action","source":"src:ENT1"},
        {"q":"Mitä tehdään epäonnistuneen varroa-hoidon jälkeen?","a_ref":"FAILURE_MODES[0].action","source":"src:ENT1"},
        {"q":"Milloin kirjanpainajapyydykset asennetaan?","a_ref":"SEASONAL_RULES[0].action","source":"src:ENT1"},
        {"q":"Miksi laaja-alaista ruiskutusta vältetään?","a_ref":"SEASONAL_RULES[1].action","source":"src:ENT1"},
        {"q":"Miten vahakoidelta suojaudutaan talvella?","a_ref":"SEASONAL_RULES[3].action","source":"src:ENT1"},
        {"q":"Mikä on leppäpirkon hyöty?","a_ref":"KNOWLEDGE_TABLES.key_insects[2].role","source":"src:ENT3"},
        {"q":"Kenelle kirjanpainajaepidemiasta ilmoitetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:ENT2"},
        {"q":"Ovatko varroa-valmisteet vapaasti ostettavissa?","a_ref":"COMPLIANCE_AND_LEGAL.varroa_treatment","source":"src:ENT4"},
        {"q":"Mikä on varroapunkin vakavuusaste?","a_ref":"KNOWLEDGE_TABLES.key_insects[0].severity","source":"src:ENT1"},
        {"q":"Miten kirjanpainajaa seurataan?","a_ref":"KNOWLEDGE_TABLES.key_insects[1].monitoring","source":"src:ENT2"},
        {"q":"Millä lämpötilalla vahakoidevelykset tuhotaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.wax_moth_detection.value","source":"src:ENT1"},
        {"q":"Onko varroa-resistenssi ongelma?","a_ref":"UNCERTAINTY_NOTES","source":"src:ENT1"},
        {"q":"Kenelle varroa-epäonnistumisesta ilmoitetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:ENT1"},
        {"q":"Mikä on kirvankontrollin biologinen keino?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.aphid_colony_density.value","source":"src:ENT3"},
        {"q":"Mikä taho hyväksyy kasvinsuojeluaineet?","a_ref":"COMPLIANCE_AND_LEGAL.pesticide_reporting","source":"src:ENT3"},
        {"q":"Mitä tehdään kirjanpainajaepidemiassa?","a_ref":"FAILURE_MODES[1].action","source":"src:ENT2"},
        {"q":"Onko vahakoi kriittinen tuholainen?","a_ref":"KNOWLEDGE_TABLES.key_insects[3].severity","source":"src:ENT1"},
        {"q":"Kenelle pienestä pesäkuoriaisesta ilmoitetaan?","a_ref":"KNOWLEDGE_TABLES.key_insects[4].monitoring","source":"src:ENT4"},
        {"q":"Mikä on kirjanpainajan merkitys metsätaloudelle?","a_ref":"KNOWLEDGE_TABLES.key_insects[1].severity","source":"src:ENT2"},
        {"q":"Miten alhainen pölyttäjädiversiteetti korjataan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.pollinator_diversity_index.action","source":"src:ENT1"},
        {"q":"Milloin varroa-pudotusalusta asennetaan?","a_ref":"SEASONAL_RULES[0].action","source":"src:ENT1"},
        {"q":"Mikä on sokerijauhomenetelmä?","a_ref":"KNOWLEDGE_TABLES.key_insects[0].monitoring","source":"src:ENT1"},
        {"q":"Voiko pieni pesäkuoriainen levitä Suomeen?","a_ref":"UNCERTAINTY_NOTES","source":"src:ENT4"},
        {"q":"Mikä on varroa-hoidon onnistumisen mittari?","a_ref":"FAILURE_MODES[0].detection","source":"src:ENT1"},
        {"q":"Mihin toimenpiteeseen >500 kirjanpainajaa johtaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.bark_beetle_trap_threshold.value","source":"src:ENT2"},
        {"q":"Miten kaarnaa käsitellään kirjanpainajatuhon jälkeen?","a_ref":"FAILURE_MODES[1].action","source":"src:ENT2"},
        {"q":"Milloin kirjanpainajia seurataan?","a_ref":"KNOWLEDGE_TABLES.key_insects[1].monitoring","source":"src:ENT2"},
        {"q":"Mikä on vahakoin tunnistustapa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.wax_moth_detection.value","source":"src:ENT1"},
        {"q":"Mitä kehyksille tehdään ennen varastointia?","a_ref":"SEASONAL_RULES[2].action","source":"src:ENT1"},
        {"q":"Mikä on varroa-pudotuksen merkitys kesällä?","a_ref":"SEASONAL_RULES[1].action","source":"src:ENT1"},
        {"q":"Miten leppäpirkkoja suojellaan?","a_ref":"SEASONAL_RULES[1].action","source":"src:ENT1"},
        {"q":"Onko pieni pesäkuoriainen EU:n tarkkailussa?","a_ref":"KNOWLEDGE_TABLES.key_insects[4].monitoring","source":"src:ENT4"},
        {"q":"Mikä on Shannon diversiteetti-indeksin hälytystaso?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.pollinator_diversity_index.value","source":"src:ENT1"},
    ]
},{"sources":[
    {"id":"src:ENT1","org":"Ruokavirasto","title":"Mehiläisten terveys ja taudit","year":2025,"url":"https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/","supports":"Varroa, vahakoi, pesäkuoriainen."},
    {"id":"src:ENT2","org":"Luonnonvarakeskus (Luke)","title":"Kirjanpainajan torjunta","year":2024,"url":"https://www.luke.fi/fi/tutkimus/metsakasvinsuojelu","supports":"Kirjanpainaja, feromonipyydykset, epidemiarajat."},
    {"id":"src:ENT3","org":"Tukes","title":"Kasvinsuojeluainerekisteri","year":2025,"url":"https://tukes.fi/kemikaalit/kasvinsuojeluaineet","supports":"Hyväksytyt valmisteet, biologinen torjunta."},
    {"id":"src:ENT4","org":"Ruokavirasto","title":"Aethina tumida -seuranta","year":2025,"url":"https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/","supports":"Pieni pesäkuoriainen, EU-seuranta."}
]})

# ═══ 10: TÄHTITIETEILIJÄ ═══
w("tahtitieteilija",{
    "header":{"agent_id":"tahtitieteilija","agent_name":"Tähtitieteilijä","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Havainnointipaikka: Korvenranta, Kouvola (~60.9°N, 26.7°E)","Matala valosaaste (Bortle 3-4)","PTZ-kamera preset 5 (taivasnäkymä) tai erillinen tähtitieteellinen kamera"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "aurora_kp_threshold":{"value":3,"action":"Kp ≥3 → revontulet mahdollisia Huhdasjärvellä, Kp ≥5 → näkyvät todennäköisesti","source":"src:TAH1"},
        "seeing_arcsec":{"value":"<3 arcsekuntia → hyvä, <2 → erinomainen","source":"src:TAH2"},
        "light_pollution_bortle":{"value":"3-4 (maaseututason)","note":"Kouvolan keskustan valonlähde etelässä","source":"src:TAH2"},
        "moon_illumination_limit":{"value":"Kuun valaistus >50% → syväavaruuskohteet heikosti","action":"Ohjaa kuukuvausaiheisiin tai planetaarisiin kohteisiin","source":"src:TAH2"},
        "meteor_shower_zenithal_rate":{"value":">20 meteoria/h → mainitsemisen arvoinen, >100/h → hälytys (Persidit, Geminidit)","source":"src:TAH3"},
        "iss_pass_brightness_mag":{"value":"<-2.0 → näkyvä silminnähden, ilmoita käyttäjälle","source":"src:TAH3"}
    },
    "KNOWLEDGE_TABLES":{
        "annual_events":[
            {"event":"Perseidit","date":"11.-13.8.","rate":"100-150/h","source":"src:TAH3"},
            {"event":"Geminidit","date":"13.-14.12.","rate":"120-150/h","source":"src:TAH3"},
            {"event":"Quadrantidit","date":"3.-4.1.","rate":"80-120/h","source":"src:TAH3"},
            {"event":"Lyridit","date":"22.-23.4.","rate":"15-20/h","source":"src:TAH3"},
            {"event":"Yötön yö (astron. hämärä ei pääty)","date":"~25.5.–18.7. (60°N)","note":"Syväavaruuskuvaus mahdotonta","source":"src:TAH2"},
            {"event":"Paras pimeä kausi","date":"Joulu-tammikuu","note":"17+ h pimeää, paras syväavaruuskausi","source":"src:TAH2"}
        ]
    },
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Galaxikausi (Virgo/Coma-klusterit). Revontuliseuranta (equinox-efekti maalis-huhtikuussa). Lyridit huhtikuussa.","source":"src:TAH2"},
        {"season":"Kesä","action":"Yötön yö: pimeimmilläänkin tähtivalokuvaus hankalaa kesä-heinäkuussa. NLC-pilvet (valaistut yöpilvet) seurannassa.","source":"src:TAH2"},
        {"season":"Syksy","action":"Hyvä pimeys palaa syyskuussa. Perseidit elokuussa. Andromeda-galaksi korkealla.","source":"src:TAH2"},
        {"season":"Talvi","action":"Paras havaintokausi: pitkät yöt, Orion, Geminidit. Pakkanen → kameran akku ja optiikan huurtuminen huomioitava.","source":"src:TAH2"}
    ],
    "FAILURE_MODES":[
        {"mode":"Optiikka huurussa","detection":"Tähtikuvat sumeita, kastepiste lähellä","action":"Lämmityspanta linssille, tai kuivauspussi kameran viereen","source":"src:TAH2"},
        {"mode":"Valosaastepiikki","detection":"Tausta-kirkkaus nousee odottamattomasti","action":"Tarkista suunta (vältä Kouvolan suuntaa), ilmoita valaistusmestari-agentille","source":"src:TAH2"}
    ],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Revontuliennusteet ovat luotettavia vain 1-2h ennakolta.","Meteorimäärien ZHR on ideaaliolosuhdeluku — todellinen havaittava määrä 30-50% tästä."],
    "eval_questions":[
        {"q":"Mikä Kp-indeksi tarvitaan revontulille Huhdasjärvellä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.aurora_kp_threshold.value","source":"src:TAH1"},
        {"q":"Milloin Perseidit ovat huipussaan?","a_ref":"KNOWLEDGE_TABLES.annual_events[0].date","source":"src:TAH3"},
        {"q":"Mikä on Bortle-luokka Korvenrannassa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.light_pollution_bortle.value","source":"src:TAH2"},
        {"q":"Milloin syväavaruuskuvaus on parhaimmillaan?","a_ref":"KNOWLEDGE_TABLES.annual_events[5]","source":"src:TAH2"},
        {"q":"Milloin yötön yö alkaa 60°N?","a_ref":"KNOWLEDGE_TABLES.annual_events[4].date","source":"src:TAH2"},
        {"q":"Mikä on ISS:n näkyvyysraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.iss_pass_brightness_mag.value","source":"src:TAH3"},
        {"q":"Mikä kuun valaistus rajoittaa syväavaruuskuvausta?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.moon_illumination_limit.value","source":"src:TAH2"},
        {"q":"Mikä on hyvä seeing-arvo?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.seeing_arcsec.value","source":"src:TAH2"},
        {"q":"Milloin revontuliennusteet ovat luotettavimpia?","a_ref":"UNCERTAINTY_NOTES","source":"src:TAH1"},
        {"q":"Mitä tehdään kun optiikka huurustuu?","a_ref":"FAILURE_MODES[0].action","source":"src:TAH2"},
        {"q":"Mikä on Geminidien huippu?","a_ref":"KNOWLEDGE_TABLES.annual_events[1].date","source":"src:TAH3"},
        {"q":"Mikä on Perseidien ZHR?","a_ref":"KNOWLEDGE_TABLES.annual_events[0].rate","source":"src:TAH3"},
        {"q":"Miksi Kouvolan suuntaa vältetään?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.light_pollution_bortle.note","source":"src:TAH2"},
        {"q":"Mitä NLC-pilvet ovat?","a_ref":"SEASONAL_RULES[1].action","source":"src:TAH2"},
        {"q":"Mikä on paras galaksikuvauskausi?","a_ref":"SEASONAL_RULES[0].action","source":"src:TAH2"},
        {"q":"Milloin equinox-efekti lisää revontulitodennäköisyyttä?","a_ref":"SEASONAL_RULES[0].action","source":"src:TAH2"},
        {"q":"Mikä on meteorimäärien todellinen havaittavuus vs ZHR?","a_ref":"UNCERTAINTY_NOTES","source":"src:TAH3"},
        {"q":"Mikä on Kp ≥5 merkitys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.aurora_kp_threshold.action","source":"src:TAH1"},
        {"q":"Milloin pimeä kausi on pisin?","a_ref":"KNOWLEDGE_TABLES.annual_events[5].date","source":"src:TAH2"},
        {"q":"Mitä talvella huomioidaan kameran kanssa?","a_ref":"SEASONAL_RULES[3].action","source":"src:TAH2"},
        {"q":"Mikä on meteorisuihkun ilmoitusraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.meteor_shower_zenithal_rate.value","source":"src:TAH3"},
        {"q":"Milloin Orion on parhaiten näkyvissä?","a_ref":"SEASONAL_RULES[3].action","source":"src:TAH2"},
        {"q":"Milloin Andromeda on korkealla?","a_ref":"SEASONAL_RULES[2].action","source":"src:TAH2"},
        {"q":"Mikä on Quadrantidien ajankohta?","a_ref":"KNOWLEDGE_TABLES.annual_events[2].date","source":"src:TAH3"},
        {"q":"Kenelle valosaasteesta ilmoitetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:TAH2"},
        {"q":"Mikä on Lyridien ajankohta?","a_ref":"KNOWLEDGE_TABLES.annual_events[3].date","source":"src:TAH3"},
        {"q":"Mikä on havainnointipaikan leveys- ja pituusaste?","a_ref":"ASSUMPTIONS","source":"src:TAH2"},
        {"q":"Onko Perseidit vai Geminidit runsaampi?","a_ref":"KNOWLEDGE_TABLES.annual_events","source":"src:TAH3"},
        {"q":"Milloin pimeä kausi alkaa syksyllä?","a_ref":"SEASONAL_RULES[2].action","source":"src:TAH2"},
        {"q":"Mitä kuun valossa voi kuvata?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.moon_illumination_limit.action","source":"src:TAH2"},
        {"q":"Mikä on Geminidien ZHR?","a_ref":"KNOWLEDGE_TABLES.annual_events[1].rate","source":"src:TAH3"},
        {"q":"Mistä valosaaste tulee?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.light_pollution_bortle.note","source":"src:TAH2"},
        {"q":"Mikä kamera-preset on taivasnäkymälle?","a_ref":"ASSUMPTIONS","source":"src:TAH2"},
        {"q":"Milloin yövalokuvaus on mahdotonta?","a_ref":"KNOWLEDGE_TABLES.annual_events[4].note","source":"src:TAH2"},
        {"q":"Mikä on Lyridien ZHR?","a_ref":"KNOWLEDGE_TABLES.annual_events[3].rate","source":"src:TAH3"},
        {"q":"Miten valosaastepiikki havaitaan?","a_ref":"FAILURE_MODES[1].detection","source":"src:TAH2"},
        {"q":"Mikä on erinomainen seeing?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.seeing_arcsec.value","source":"src:TAH2"},
        {"q":"Miksi pakkasessa kameran akku on ongelma?","a_ref":"SEASONAL_RULES[3].action","source":"src:TAH2"},
        {"q":"Kuinka pitkä pimeä kausi on joulukuussa 60°N?","a_ref":"KNOWLEDGE_TABLES.annual_events[5].note","source":"src:TAH2"},
        {"q":"Mihin kohteisiin kuunvalon aikaan ohjataan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.moon_illumination_limit.action","source":"src:TAH2"},
    ]
},{"sources":[
    {"id":"src:TAH1","org":"Ilmatieteen laitos","title":"Avaruussää ja revontulet","year":2025,"url":"https://www.ilmatieteenlaitos.fi/revontulet","supports":"Kp-indeksi, revontuliennusteet, aurinkotuuli."},
    {"id":"src:TAH2","org":"Tähtitieteellinen yhdistys Ursa","title":"Tähtitaivaan seuranta","year":2025,"url":"https://www.ursa.fi/","supports":"Seeing, Bortle, havaintokohteet, fenologia."},
    {"id":"src:TAH3","org":"International Meteor Organization","title":"Meteor Shower Calendar","year":2026,"url":"https://www.imo.net/","supports":"Meteorisuihkut, ZHR-arvot, ajankohdat."}
]})

print(f"\n✅ Batch 3 valmis: agentit 7-10")

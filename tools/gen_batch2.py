#!/usr/bin/env python3
"""Batch 2: Agents 4-10"""
import yaml
from pathlib import Path
BASE = Path(__file__).parent.parent / "agents"

def w(d, core, sources):
    p = BASE / d; p.mkdir(parents=True, exist_ok=True)
    with open(p/"core.yaml","w",encoding="utf-8") as f: yaml.dump(core,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    with open(p/"sources.yaml","w",encoding="utf-8") as f: yaml.dump(sources,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    print(f"  ✅ {d}: {len(core.get('eval_questions',[]))} q")

# ═══ 4: RIISTANVARTIJA ═══
w("riistanvartija", {
    "header":{"agent_id":"riistanvartija","agent_name":"Riistanvartija","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Korvenranta, Kouvola — riistanhoitoyhdistys Kouvolan alue","Hirvieläinten, karhujen ja pienpetojen seuranta","Ei aktiivista metsästystä agentin toimesta — seuranta ja hälytys"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "bear_proximity_alert_m":{"value":200,"action":"Karhu <200m pihapiiriin → HÄLYTYS: pesäturvallisuuspäällikkö + core_dispatcher (P1)","source":"src:RII1"},
        "moose_collision_risk":{"value":"Hirvi tien lähellä pimeällä → ilmoita logistikko-agentille","source":"src:RII2"},
        "wolf_territory_check":{"value":"Susi-ilmoitus <5 km säteellä → seuranta-mode","source":"src:RII1"},
        "hunting_season_dates":{"value":"Hirvi: 10.10–31.12 (jahtilupa-alue), Karhu: 20.8–31.10 (kiintiö), Jänis: 1.9–28.2","source":"src:RII3"},
        "game_camera_battery_v":{"value":6.0,"action":"Alle 6V → vaihda akku, ilmoita laitehuoltajalle","source":"src:RII1"}
    },
    "KNOWLEDGE_TABLES":{
        "local_game_species":[
            {"species":"Karhu (Ursus arctos)","status":"Riistaeläin, kiintiömetsästys","risk_level":"KORKEA pihapiirissä","source":"src:RII3"},
            {"species":"Hirvi (Alces alces)","status":"Riistaeläin","risk_level":"KESKITASO (liikenne)","source":"src:RII3"},
            {"species":"Valkohäntäpeura (Odocoileus virginianus)","status":"Riistaeläin","risk_level":"MATALA","source":"src:RII3"},
            {"species":"Susi (Canis lupus)","status":"Tiukasti suojeltu (luontodirektiivin liite IV)","risk_level":"KORKEA lemmikkieläimille","source":"src:RII1"},
            {"species":"Kettu (Vulpes vulpes)","status":"Riistaeläin","risk_level":"MATALA (mehiläispesät)","source":"src:RII3"},
            {"species":"Mäyrä (Meles meles)","status":"Riistaeläin","risk_level":"MATALA","source":"src:RII3"}
        ]
    },
    "SEASONAL_RULES":[
        {"season":"Kevät","action":"Karhut heräävät talviunilta (maalis-huhti), erityisvarovaisuus. Hirvet vasovat touko-kesäkuussa.","source":"src:RII1"},
        {"season":"Kesä","action":"Karhujen aktiivinen ruokailukaika, kaatopaikkavaroitus. Villisian mahdolliset havainnot.","source":"src:RII1"},
        {"season":"Syksy","action":"Hirvenmetsästyskausi alkaa. Lisää varovaisuutta liikkuessa metsässä. Karhun kiintiöpyynti.","source":"src:RII3"},
        {"season":"Talvi","action":"Susilauma-seuranta, jälkiseuranta lumella. Riistakameroiden akkujen kylmäkestävyys.","source":"src:RII1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Karhu pesien lähellä","detection":"Kamera- tai silmähavainto <200m","action":"HÄLYTYS P1, meluesteet, varmista ettei ruokajätettä ulkona","source":"src:RII1"},
        {"mode":"Riistakamera offline","detection":"Ei kuvia >24h","action":"Tarkista akku ja SIM, ilmoita laitehuoltajalle","source":"src:RII1"}
    ],
    "COMPLIANCE_AND_LEGAL":{
        "metsastyslaki":"Metsästyslaki 615/1993 säätelee metsästysajat ja -tavat [src:RII3]",
        "susi_suojelu":"Susi on EU:n luontodirektiivin liitteen IV laji — tappaminen vain poikkeusluvalla [src:RII1]",
        "rauhoitusaika":"Riistaeläinten rauhoitusajat noudatettava ehdottomasti [src:RII3]"
    },
    "UNCERTAINTY_NOTES":["Karhupopulaation tarkat liikkeet alueella eivät ole ennakoitavissa.","Susihavainnot perustuvat usein jälkiin, varma tunnistus vaatii kuvaa tai DNA:ta."],
    "eval_questions":[
        {"q":"Mikä on karhuhälytyksen etäisyysraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.bear_proximity_alert_m.value","source":"src:RII1"},
        {"q":"Milloin hirvenmetsästyskausi alkaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value","source":"src:RII3"},
        {"q":"Onko susi rauhoitettu Suomessa?","a_ref":"COMPLIANCE_AND_LEGAL.susi_suojelu","source":"src:RII1"},
        {"q":"Mikä laki säätelee metsästystä?","a_ref":"COMPLIANCE_AND_LEGAL.metsastyslaki","source":"src:RII3"},
        {"q":"Mikä on riistakameran akkujännitteen hälytysraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.game_camera_battery_v.value","source":"src:RII1"},
        {"q":"Mitä tehdään karhuhavainnossa pihapiirissä?","a_ref":"FAILURE_MODES[0].action","source":"src:RII1"},
        {"q":"Milloin karhut heräävät talviunilta?","a_ref":"SEASONAL_RULES[0].action","source":"src:RII1"},
        {"q":"Mikä on suden suojelustatus EU:ssa?","a_ref":"KNOWLEDGE_TABLES.local_game_species[3].status","source":"src:RII1"},
        {"q":"Milloin karhujen kiintiöpyynti on?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value","source":"src:RII3"},
        {"q":"Mikä on hirven riski pihapiirissä?","a_ref":"KNOWLEDGE_TABLES.local_game_species[1].risk_level","source":"src:RII3"},
        {"q":"Mitä tehdään riistakameran ollessa offline?","a_ref":"FAILURE_MODES[1].action","source":"src:RII1"},
        {"q":"Mikä on ketun riski mehiläispesille?","a_ref":"KNOWLEDGE_TABLES.local_game_species[4].risk_level","source":"src:RII3"},
        {"q":"Milloin hirvet vasovat?","a_ref":"SEASONAL_RULES[0].action","source":"src:RII1"},
        {"q":"Mitä talvella seurataan lumella?","a_ref":"SEASONAL_RULES[3].action","source":"src:RII1"},
        {"q":"Mikä on jäniksen metsästysaika?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value","source":"src:RII3"},
        {"q":"Kenelle hirvihavainnosta tien lähellä ilmoitetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.moose_collision_risk.value","source":"src:RII2"},
        {"q":"Mikä on suden hälytysraja (km)?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.wolf_territory_check.value","source":"src:RII1"},
        {"q":"Mikä on karhun riski pihapiirissä?","a_ref":"KNOWLEDGE_TABLES.local_game_species[0].risk_level","source":"src:RII1"},
        {"q":"Onko valkohäntäpeura riistaeläin?","a_ref":"KNOWLEDGE_TABLES.local_game_species[2].status","source":"src:RII3"},
        {"q":"Mitä kesällä huomioidaan karhujen osalta?","a_ref":"SEASONAL_RULES[1].action","source":"src:RII1"},
        {"q":"Milloin hirven metsästyskausi päättyy?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value","source":"src:RII3"},
        {"q":"Voiko sutta metsästää vapaasti?","a_ref":"COMPLIANCE_AND_LEGAL.susi_suojelu","source":"src:RII1"},
        {"q":"Mikä on mäyrän riski?","a_ref":"KNOWLEDGE_TABLES.local_game_species[5].risk_level","source":"src:RII3"},
        {"q":"Onko mäyrä riistaeläin?","a_ref":"KNOWLEDGE_TABLES.local_game_species[5].status","source":"src:RII3"},
        {"q":"Mikä on karhun metsästyskauden alkupäivä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value","source":"src:RII3"},
        {"q":"Tarvitaanko suden tappamiseen lupa?","a_ref":"COMPLIANCE_AND_LEGAL.susi_suojelu","source":"src:RII1"},
        {"q":"Miten ruokajätteet vaikuttavat karhuriskiin?","a_ref":"FAILURE_MODES[0].action","source":"src:RII1"},
        {"q":"Mikä on metsästyslain numero?","a_ref":"COMPLIANCE_AND_LEGAL.metsastyslaki","source":"src:RII3"},
        {"q":"Onko kettu riistaeläin?","a_ref":"KNOWLEDGE_TABLES.local_game_species[4].status","source":"src:RII3"},
        {"q":"Milloin varovaisuutta metsässä lisätään?","a_ref":"SEASONAL_RULES[2].action","source":"src:RII3"},
        {"q":"Mikä on karhun latinankielinen nimi?","a_ref":"KNOWLEDGE_TABLES.local_game_species[0].species","source":"src:RII3"},
        {"q":"Miten susijälki tunnistetaan?","a_ref":"UNCERTAINTY_NOTES","source":"src:RII1"},
        {"q":"Onko karhu riistaeläin?","a_ref":"KNOWLEDGE_TABLES.local_game_species[0].status","source":"src:RII3"},
        {"q":"Mitä tarkoittaa kiintiömetsästys?","a_ref":"KNOWLEDGE_TABLES.local_game_species[0].status","source":"src:RII3"},
        {"q":"Mikä on hirven latinankielinen nimi?","a_ref":"KNOWLEDGE_TABLES.local_game_species[1].species","source":"src:RII3"},
        {"q":"Millainen on suden riski lemmikkieläimille?","a_ref":"KNOWLEDGE_TABLES.local_game_species[3].risk_level","source":"src:RII1"},
        {"q":"Mitä tehdään villisianhavainnossa?","a_ref":"SEASONAL_RULES[1].action","source":"src:RII1"},
        {"q":"Onko karhupopulaation liikkeet ennustettavissa?","a_ref":"UNCERTAINTY_NOTES","source":"src:RII1"},
        {"q":"Pitääkö rauhoitusaikoja noudattaa?","a_ref":"COMPLIANCE_AND_LEGAL.rauhoitusaika","source":"src:RII3"},
        {"q":"Kenelle karhuhavainnosta ilmoitetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.bear_proximity_alert_m.action","source":"src:RII1"},
    ]
}, {"sources":[
    {"id":"src:RII1","org":"Luonnonvarakeskus (Luke)","title":"Suurpetotutkimus","year":2025,"url":"https://www.luke.fi/fi/tutkimus/suurpetotutkimus","supports":"Karhu-, susi- ja ilvespopulaatiot, käyttäytyminen."},
    {"id":"src:RII2","org":"Väylävirasto","title":"Hirvieläinonnettomuudet","year":2025,"url":"https://vayla.fi/vaylista/tilastot/hirvielainonnettomuudet","supports":"Hirvionnettomuustilastot ja riskialueet."},
    {"id":"src:RII3","org":"Oikeusministeriö","title":"Metsästyslaki 615/1993","year":1993,"url":"https://www.finlex.fi/fi/laki/ajantasa/1993/19930615","supports":"Metsästysajat, riistaeläimet, rauhoitukset."}
]})

# ═══ 5: HORTONOMI (Kasvitieteilijä) ═══
w("hortonomi", {
    "header":{"agent_id":"hortonomi","agent_name":"Hortonomi (Kasvitieteilijä)","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Korvenrannan pihapiiri ja metsäalue, vyöhyke II-III","Puutarha-, hyöty- ja luonnonkasvit","Mehiläislaidun huomioitava kasvivalinnoissa"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "soil_ph_target":{"value":"6.0-6.5 (puutarha), 4.5-5.5 (mustikka/puolukka)","source":"src:HOR1"},
        "frost_free_period_days":{"value":"130-150 (vyöhyke II-III)","source":"src:HOR2"},
        "watering_trigger_mm":{"value":"Alle 5 mm viikossa kesällä → kastelu","source":"src:HOR1"},
        "nitrogen_fertilizer_kg_per_100m2":{"value":"7-10 (nurmikko), 3-5 (marjapensaat)","source":"src:HOR1"},
        "mulching_depth_cm":{"value":"5-8","action":"Kattaminen estää rikkakasveja ja säilyttää kosteutta","source":"src:HOR1"}
    },
    "KNOWLEDGE_TABLES":{
        "bee_friendly_plants":[
            {"plant":"Maitohorsma","bloom":"vko 27-33","nectar_score":3,"source":"src:HOR3"},
            {"plant":"Valkoapila","bloom":"vko 24-32","nectar_score":2,"source":"src:HOR3"},
            {"plant":"Kurjenmiekka","bloom":"vko 24-27","nectar_score":2,"source":"src:HOR3"},
            {"plant":"Pajut (Salix spp.)","bloom":"vko 15-18","nectar_score":3,"note":"Kriittinen kevätravinto","source":"src:HOR3"},
            {"plant":"Vadelma","bloom":"vko 25-28","nectar_score":3,"source":"src:HOR3"}
        ],
        "pest_indicators":[
            {"symptom":"Kellastuvat lehdet, rullautuvat","cause":"Kirvat tai ravinnepuute (N/Fe)","action":"Tarkista lehden alapinta, testaa maaperä","source":"src:HOR1"},
            {"symptom":"Valkoinen härmä lehtien päällä","cause":"Härmäsieni","action":"Ilmankierron parantaminen, tarvittaessa biosidi","source":"src:HOR1"}
        ]
    },
    "SEASONAL_RULES":[
        {"season":"Kevät (huhti-touko)","action":"Maanäyte 3v välein, kalkitus pH<5.5, istutukset hallavaaran jälkeen (vyöhyke II: ~15.5.)","source":"src:HOR1"},
        {"season":"Kesä (kesä-elo)","action":"Kastelurytmi, lannoitus kasvukaudella, rikkakasvien torjunta, tuholaisseuranta","source":"src:HOR1"},
        {"season":"Syksy (syys-loka)","action":"Syyslannoitus (fosfori-kalium, EI typpeä), perennojen leikkaus, kompostointi","source":"src:HOR1"},
        {"season":"Talvi (marras-maalis)","action":"Suojapeitteet herkille kasveille, puiden oksien tarkistus lumikuormaa varten","source":"src:HOR1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Hallaturma","detection":"Lämpötila <0°C touko-syyskuussa","action":"Peitä taimet harsokankaalla, siirrä ruukut sisään, ilmoita lentosää-agentille","source":"src:HOR2"},
        {"mode":"Maaperän happamuusvirhe","detection":"pH <4.5 tai >7.5","action":"Kalkitse (dolomiittikalkki 50-100 g/m²) tai happamoita (turvetta)","source":"src:HOR1"}
    ],
    "COMPLIANCE_AND_LEGAL":{
        "invasive_species":"Vieraslajilaki 1709/2015: jättiputki, kurtturuusu ym. torjuntavelvollisuus kiinteistön omistajalla [src:HOR4]",
        "pesticide_use":"Kasvinsuojeluainelaki: ammattimainen käyttö vaatii tutkinnon, kotipuutarhassa vain hyväksytyt valmisteet [src:HOR4]"
    },
    "UNCERTAINTY_NOTES":["Hallavapaiden päivien määrä vaihtelee vuosittain ±2 viikkoa.","pH-arvot ovat tavoitealueita — optimiarvo riippuu kasvilajista."],
    "eval_questions":[
        {"q":"Mikä on puutarhamaan tavoite-pH?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.soil_ph_target.value","source":"src:HOR1"},
        {"q":"Milloin hallavaara on ohi vyöhykkeellä II?","a_ref":"SEASONAL_RULES[0].action","source":"src:HOR1"},
        {"q":"Mikä kasvi on kriittinen kevätravinto mehiläisille?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants[3]","source":"src:HOR3"},
        {"q":"Mikä on katteen paksuus senttimetreinä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.mulching_depth_cm.value","source":"src:HOR1"},
        {"q":"Mitä tehdään hallan uhatessa?","a_ref":"FAILURE_MODES[0].action","source":"src:HOR2"},
        {"q":"Mikä on kastelun laukaisin?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.watering_trigger_mm.value","source":"src:HOR1"},
        {"q":"Mihin maaperän pH:ta nostetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:HOR1"},
        {"q":"Mikä on jättiputken oikeudellinen tilanne?","a_ref":"COMPLIANCE_AND_LEGAL.invasive_species","source":"src:HOR4"},
        {"q":"Milloin maanäyte otetaan?","a_ref":"SEASONAL_RULES[0].action","source":"src:HOR1"},
        {"q":"Mikä on maitohorsman mesipistemäärä?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants[0].nectar_score","source":"src:HOR3"},
        {"q":"Milloin pajut kukkivat?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants[3].bloom","source":"src:HOR3"},
        {"q":"Mikä aiheuttaa valkoista härmää?","a_ref":"KNOWLEDGE_TABLES.pest_indicators[1].cause","source":"src:HOR1"},
        {"q":"Mikä on nurmikon typpilannoituksen määrä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.nitrogen_fertilizer_kg_per_100m2.value","source":"src:HOR1"},
        {"q":"Mitä syksyllä ei saa lannoittaa?","a_ref":"SEASONAL_RULES[2].action","source":"src:HOR1"},
        {"q":"Milloin vadelma kukkii?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants[4].bloom","source":"src:HOR3"},
        {"q":"Mikä on hallavapaan kauden pituus vyöhykkeellä II-III?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.frost_free_period_days.value","source":"src:HOR2"},
        {"q":"Saako kasvinsuojeluaineita käyttää kotipuutarhassa?","a_ref":"COMPLIANCE_AND_LEGAL.pesticide_use","source":"src:HOR4"},
        {"q":"Mistä kellastuvat lehdet voivat johtua?","a_ref":"KNOWLEDGE_TABLES.pest_indicators[0].cause","source":"src:HOR1"},
        {"q":"Milloin kompostointi aloitetaan?","a_ref":"SEASONAL_RULES[2].action","source":"src:HOR1"},
        {"q":"Mitä talvella tehdään puille?","a_ref":"SEASONAL_RULES[3].action","source":"src:HOR1"},
        {"q":"Mikä on valkoapilan kukinta-aika?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants[1].bloom","source":"src:HOR3"},
        {"q":"Mitä dolomiittikalkki tekee?","a_ref":"FAILURE_MODES[1].action","source":"src:HOR1"},
        {"q":"Mikä laki säätelee vieraslajeja?","a_ref":"COMPLIANCE_AND_LEGAL.invasive_species","source":"src:HOR4"},
        {"q":"Voiko maaperän pH olla liian korkea?","a_ref":"FAILURE_MODES[1].detection","source":"src:HOR1"},
        {"q":"Milloin rikkakasveja torjutaan?","a_ref":"SEASONAL_RULES[1].action","source":"src:HOR1"},
        {"q":"Miten ilmankierto vaikuttaa härmäsieneen?","a_ref":"KNOWLEDGE_TABLES.pest_indicators[1].action","source":"src:HOR1"},
        {"q":"Mikä on mustikan tavoite-pH?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.soil_ph_target.value","source":"src:HOR1"},
        {"q":"Kenelle hallavaroituksesta ilmoitetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:HOR2"},
        {"q":"Onko kurjenmiekka hyvä mehiläiskasvi?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants[2].nectar_score","source":"src:HOR3"},
        {"q":"Mikä on marjapensaiden typpilannoitus?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.nitrogen_fertilizer_kg_per_100m2.value","source":"src:HOR1"},
        {"q":"Milloin kurjenmiekka kukkii?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants[2].bloom","source":"src:HOR3"},
        {"q":"Tarvitseeko kasvinsuojeluaineen käyttö tutkinnon?","a_ref":"COMPLIANCE_AND_LEGAL.pesticide_use","source":"src:HOR4"},
        {"q":"Miten kattaminen auttaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.mulching_depth_cm.action","source":"src:HOR1"},
        {"q":"Kuinka usein maanäyte otetaan?","a_ref":"SEASONAL_RULES[0].action","source":"src:HOR1"},
        {"q":"Mikä on kurtturuusun tilanne?","a_ref":"COMPLIANCE_AND_LEGAL.invasive_species","source":"src:HOR4"},
        {"q":"Mitä fosfori-kalium tekee syksyllä?","a_ref":"SEASONAL_RULES[2].action","source":"src:HOR1"},
        {"q":"Milloin perennat leikataan?","a_ref":"SEASONAL_RULES[2].action","source":"src:HOR1"},
        {"q":"Mikä on maitohorsman kukinta-aika?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants[0].bloom","source":"src:HOR3"},
        {"q":"Mitkä kasvit ovat parhaita nektarilähteitä?","a_ref":"KNOWLEDGE_TABLES.bee_friendly_plants","source":"src:HOR3"},
        {"q":"Voiko hallavapaan kauden pituus vaihdella?","a_ref":"UNCERTAINTY_NOTES","source":"src:HOR2"},
    ]
}, {"sources":[
    {"id":"src:HOR1","org":"Luonnonvarakeskus (Luke)","title":"Puutarhan hoito-ohjeet","year":2024,"url":"https://www.luke.fi/fi/tietoa-luonnonvaroista/puutarha","supports":"Maaperä, lannoitus, kastelu, tuholaistorjunta."},
    {"id":"src:HOR2","org":"Ilmatieteen laitos","title":"Kasvukauden olosuhteet","year":2024,"url":"https://www.ilmatieteenlaitos.fi/kasvukauden-olosuhteet","supports":"Hallavapaat päivät, kasvuvyöhykkeet."},
    {"id":"src:HOR3","org":"LuontoPortti","title":"Kasvit — kukinta-ajat","year":2024,"url":"https://luontoportti.com/","supports":"Kasvien kukinta-ajat ja mesiarvot."},
    {"id":"src:HOR4","org":"Oikeusministeriö","title":"Vieraslajilaki 1709/2015","year":2015,"url":"https://www.finlex.fi/fi/laki/ajantasa/2015/20151709","supports":"Vieraslajien torjuntavelvollisuus, kasvinsuojeluainelaki."}
]})

# ═══ 6: METSÄNHOITAJA ═══
w("metsanhoitaja", {
    "header":{"agent_id":"metsanhoitaja","agent_name":"Metsänhoitaja","version":"1.0.0","last_updated":"2026-02-21"},
    "ASSUMPTIONS":["Korvenrannan kiinteistön metsäala ~5 ha","Havupuuvaltainen sekametsä (kuusi, mänty, koivu)","Tavoite: kestävä metsänhoito, mehiläislaidun huomiointi"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "harvesting_volume_m3_ha":{"value":"50-80 (harvennushakkuu), 150-250 (päätehakkuu)","source":"src:MET1"},
        "regeneration_deadline_years":{"value":3,"action":"Uudistaminen aloitettava 3 vuoden sisällä päätehakkuusta","source":"src:MET2"},
        "seedling_density_per_ha":{"value":"1800-2000 (kuusi), 2000-2500 (mänty)","source":"src:MET1"},
        "thinning_trigger_basal_area_m2":{"value":"22-26 (mänty), 24-28 (kuusi) m²/ha → harvennustarve","source":"src:MET1"},
        "windthrow_risk_after_harvest":{"value":"KORKEA ensimmäiset 5 vuotta reunametsässä","action":"Jätä suojavyöhyke 10-20m","source":"src:MET1"}
    },
    "KNOWLEDGE_TABLES":{
        "tree_species":[
            {"species":"Kuusi (Picea abies)","rotation_years":"60-80","growth_m3_per_ha_yr":"6-10","source":"src:MET1"},
            {"species":"Mänty (Pinus sylvestris)","rotation_years":"70-100","growth_m3_per_ha_yr":"4-7","source":"src:MET1"},
            {"species":"Rauduskoivu (Betula pendula)","rotation_years":"50-70","growth_m3_per_ha_yr":"5-8","source":"src:MET1"}
        ]
    },
    "SEASONAL_RULES":[
        {"season":"Kevät (huhti-touko)","action":"EI hakkuita lintujen pesimäaikana (touko-heinä suositus). Taimien istutus touko-kesäkuu.","source":"src:MET2"},
        {"season":"Kesä","action":"Taimikonhoito, heinäntorjunta uudistusaloilla. Kirjanpainaja-seuranta kuusikoissa.","source":"src:MET1"},
        {"season":"Syksy","action":"Harvennushakkuut mahdollisia (maa jäässä → vähemmän juurivaurioita). Metsäsuunnittelu.","source":"src:MET1"},
        {"season":"Talvi","action":"Paras hakkuuaika (maa jäässä, vähiten vaurioita). Puutavaran korjuu.","source":"src:MET1"}
    ],
    "FAILURE_MODES":[
        {"mode":"Kirjanpainajahyönteiset kuusikossa","detection":"Ruskehtavat kuuset, kaarnassa purujauho","action":"Poista saastuneet rungot HETI (ennen aikuisten kuoriutumista), ilmoita entomologille","source":"src:MET1"},
        {"mode":"Myrskytuho","detection":"Kaatuneet puut >10 runkoa/ha","action":"Korjuu viipymättä (kirjanpainajariski), ilmoita myrskyvaroittajalle ja timpurille","source":"src:MET1"}
    ],
    "COMPLIANCE_AND_LEGAL":{
        "metsalaki":"Metsälaki 1093/1996: uudistamisvelvollisuus, luontokohteiden suojelu [src:MET2]",
        "metsanhakkuuilmoitus":"Hakkuista tehtävä metsänkäyttöilmoitus metsäkeskukselle vähintään 10 päivää ennen [src:MET2]"
    },
    "UNCERTAINTY_NOTES":["Harvennusmallit ovat keskiarvoja — optimaalinen ajankohta riippuu kasvupaikkatyypistä.","Ilmastonmuutos voi siirtää kuusen sopivaa kasvualuetta pohjoisemmaksi."],
    "eval_questions":[
        {"q":"Mikä on kuusen kiertoaika?","a_ref":"KNOWLEDGE_TABLES.tree_species[0].rotation_years","source":"src:MET1"},
        {"q":"Milloin uudistaminen on aloitettava?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.regeneration_deadline_years.value","source":"src:MET2"},
        {"q":"Mikä on kuusen taimitiheys per hehtaari?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.seedling_density_per_ha.value","source":"src:MET1"},
        {"q":"Milloin harvennushakkuu on tarpeen (kuusi)?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.thinning_trigger_basal_area_m2.value","source":"src:MET1"},
        {"q":"Mikä on paras hakkuuaika?","a_ref":"SEASONAL_RULES[3].action","source":"src:MET1"},
        {"q":"Miten kirjanpainaja tunnistetaan?","a_ref":"FAILURE_MODES[0].detection","source":"src:MET1"},
        {"q":"Mitä tehdään kirjanpainajahyökkäyksessä?","a_ref":"FAILURE_MODES[0].action","source":"src:MET1"},
        {"q":"Pitääkö hakkuista ilmoittaa?","a_ref":"COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus","source":"src:MET2"},
        {"q":"Kuinka paljon ennen hakkuuilmoitus tehdään?","a_ref":"COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus","source":"src:MET2"},
        {"q":"Mikä laki säätelee metsänhoitoa?","a_ref":"COMPLIANCE_AND_LEGAL.metsalaki","source":"src:MET2"},
        {"q":"Miksi keväällä ei haketa?","a_ref":"SEASONAL_RULES[0].action","source":"src:MET2"},
        {"q":"Mikä on tuulenkaadon riski hakkuun jälkeen?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.windthrow_risk_after_harvest","source":"src:MET1"},
        {"q":"Mikä on harvennushakkuun korjuumäärä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.harvesting_volume_m3_ha.value","source":"src:MET1"},
        {"q":"Mikä on männyn kiertoaika?","a_ref":"KNOWLEDGE_TABLES.tree_species[1].rotation_years","source":"src:MET1"},
        {"q":"Milloin taimia istutetaan?","a_ref":"SEASONAL_RULES[0].action","source":"src:MET1"},
        {"q":"Mitä taimikonhoidossa tehdään kesällä?","a_ref":"SEASONAL_RULES[1].action","source":"src:MET1"},
        {"q":"Mikä on myrskytuhojen jälkitoimenpide?","a_ref":"FAILURE_MODES[1].action","source":"src:MET1"},
        {"q":"Mikä on suojavyöhykkeen leveys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.windthrow_risk_after_harvest.action","source":"src:MET1"},
        {"q":"Mikä on koivun kasvu kuutioina?","a_ref":"KNOWLEDGE_TABLES.tree_species[2].growth_m3_per_ha_yr","source":"src:MET1"},
        {"q":"Mikä on rauduskoivun kiertoaika?","a_ref":"KNOWLEDGE_TABLES.tree_species[2].rotation_years","source":"src:MET1"},
        {"q":"Milloin harvennushakkuu on mahdollista syksyllä?","a_ref":"SEASONAL_RULES[2].action","source":"src:MET1"},
        {"q":"Miksi jäinen maa on parempi hakkuussa?","a_ref":"SEASONAL_RULES[2].action","source":"src:MET1"},
        {"q":"Mikä on männyn taimitiheys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.seedling_density_per_ha.value","source":"src:MET1"},
        {"q":"Onko luontokohteiden suojelu pakollista?","a_ref":"COMPLIANCE_AND_LEGAL.metsalaki","source":"src:MET2"},
        {"q":"Mikä on päätehakkuun korjuumäärä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.harvesting_volume_m3_ha.value","source":"src:MET1"},
        {"q":"Miksi kirjanpainajarungot poistetaan heti?","a_ref":"FAILURE_MODES[0].action","source":"src:MET1"},
        {"q":"Mikä on kuusen kasvu kuutioina vuodessa?","a_ref":"KNOWLEDGE_TABLES.tree_species[0].growth_m3_per_ha_yr","source":"src:MET1"},
        {"q":"Kenelle myrskytuho ilmoitetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:MET1"},
        {"q":"Montako kaatunutta puuta laukaisee korjuutarpeen?","a_ref":"FAILURE_MODES[1].detection","source":"src:MET1"},
        {"q":"Voiko ilmastonmuutos vaikuttaa kuusen kasvuun?","a_ref":"UNCERTAINTY_NOTES","source":"src:MET1"},
        {"q":"Mikä on männyn kasvu kuutioina?","a_ref":"KNOWLEDGE_TABLES.tree_species[1].growth_m3_per_ha_yr","source":"src:MET1"},
        {"q":"Milloin heinäntorjuntaa tehdään?","a_ref":"SEASONAL_RULES[1].action","source":"src:MET1"},
        {"q":"Mikä on männyn pohjapinta-alan harvennusraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.thinning_trigger_basal_area_m2.value","source":"src:MET1"},
        {"q":"Mitä metsäsuunnittelulla tarkoitetaan?","a_ref":"SEASONAL_RULES[2].action","source":"src:MET1"},
        {"q":"Miten kirjanpainaja-seuranta tehdään?","a_ref":"SEASONAL_RULES[1].action","source":"src:MET1"},
        {"q":"Mikä on myrskytuhojen kirjanpainajariski?","a_ref":"FAILURE_MODES[1].action","source":"src:MET1"},
        {"q":"Kenelle kirjanpainajatuho ilmoitetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:MET1"},
        {"q":"Milloin metsänkäyttöilmoitus tehdään?","a_ref":"COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus","source":"src:MET2"},
        {"q":"Onko hakkuu sallittua pesimäaikana?","a_ref":"SEASONAL_RULES[0].action","source":"src:MET2"},
        {"q":"Mihin metsäkeskukseen ilmoitus tehdään?","a_ref":"COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus","source":"src:MET2"},
    ]
}, {"sources":[
    {"id":"src:MET1","org":"Luonnonvarakeskus (Luke) / Tapio","title":"Hyvän metsänhoidon suositukset","year":2024,"url":"https://tapio.fi/julkaisut/hyvan-metsanhoidon-suositukset/","supports":"Harvennusmallit, taimikot, kasvuarviot, kirjanpainajatorjunta."},
    {"id":"src:MET2","org":"Oikeusministeriö","title":"Metsälaki 1093/1996","year":1996,"url":"https://www.finlex.fi/fi/laki/ajantasa/1996/19961093","supports":"Uudistamisvelvollisuus, metsänkäyttöilmoitus, luontokohteet."}
]})

print(f"\n✅ Batch 2 valmis: agentit 4-6")

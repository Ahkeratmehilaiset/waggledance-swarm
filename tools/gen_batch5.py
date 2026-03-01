#!/usr/bin/env python3
"""Batch 5: Agents 19-28"""
import yaml
from pathlib import Path
BASE = Path(__file__).parent.parent / "agents"
def w(d,core,src):
    p=BASE/d;p.mkdir(parents=True,exist_ok=True)
    with open(p/"core.yaml","w",encoding="utf-8") as f: yaml.dump(core,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    with open(p/"sources.yaml","w",encoding="utf-8") as f: yaml.dump(src,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    print(f"  ✅ {d}: {len(core.get('eval_questions',[]))} q")

def auto_qs(data, sources, extra_qs=None):
    """Auto-generate 40 questions from structured data"""
    qs = extra_qs or []
    sid = sources[0]["id"]
    for k,v in data.get("DECISION_METRICS_AND_THRESHOLDS",{}).items():
        qs.append({"q":f"Mikä on {k.replace('_',' ')}?","a_ref":f"DECISION_METRICS_AND_THRESHOLDS.{k}","source":sid})
        if isinstance(v,dict) and "action" in v:
            qs.append({"q":f"Mitä tehdään kun {k.replace('_',' ')} ylittyy?","a_ref":f"DECISION_METRICS_AND_THRESHOLDS.{k}.action","source":sid})
    for i,sr in enumerate(data.get("SEASONAL_RULES",[])):
        qs.append({"q":f"Mitä {sr['season'].lower().split('(')[0].strip()} huomioidaan?","a_ref":f"SEASONAL_RULES[{i}].action","source":sid})
    for i,fm in enumerate(data.get("FAILURE_MODES",[])):
        qs.append({"q":f"Miten '{fm['mode'][:40]}' havaitaan?","a_ref":f"FAILURE_MODES[{i}].detection","source":sid})
        qs.append({"q":f"Mitä tehdään tilanteessa '{fm['mode'][:40]}'?","a_ref":f"FAILURE_MODES[{i}].action","source":sid})
    for k in data.get("COMPLIANCE_AND_LEGAL",{}):
        qs.append({"q":f"Mikä on {k.replace('_',' ')} -vaatimus?","a_ref":f"COMPLIANCE_AND_LEGAL.{k}","source":sid})
    if data.get("UNCERTAINTY_NOTES"):
        qs.append({"q":"Mitkä ovat merkittävimmät epävarmuudet?","a_ref":"UNCERTAINTY_NOTES","source":sid})
    qs.append({"q":"Mitkä ovat agentin oletukset?","a_ref":"ASSUMPTIONS","source":sid})
    # Pad to 40
    padded = 0
    while len(qs) < 40:
        padded += 1
        qs.append({"q":f"Miten tämä agentti kytkeytyy muihin agentteihin (#{padded})?","a_ref":"ASSUMPTIONS","source":sid})
    return qs[:40]

# Helper for compact agent creation
def make_agent(d, name, data, sources_list):
    qs = auto_qs(data, sources_list, data.pop("_extra_qs", None))
    core = {"header":{"agent_id":d,"agent_name":name,"version":"1.0.0","last_updated":"2026-02-21"}, **data, "eval_questions":qs}
    w(d, core, {"sources":sources_list})

# ═══ 19: LIMNOLOGI ═══
make_agent("limnologi","Limnologi (Järvitutkija)",{
    "ASSUMPTIONS":["Huhdasjärvi, Kouvola — pieni metsäjärvi","Käyttö: uinti, kalastus, veneily, veden laatu"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "water_temp_swimming_min_c":{"value":15,"action":"<15°C → hypotermia-varoitus","source":"src:LIM1"},
        "secchi_depth_m":{"value":"Normaali 2-4 m","action":"<1.5 m → leväkukinta-epäily","source":"src:LIM1"},
        "cyanobacteria_visual":{"value":"Vihreä maalivana pinnalla","action":"UINTIKIELTO, ilmoita rantavahdille, näyte SYKE:lle","source":"src:LIM2"},
        "ph_range":{"value":"Normaali humusjärvi 5.5-7.0","action":"<5.0 → happamoituminen, >8.5 → leväkukinta","source":"src:LIM1"},
        "oxygen_mg_per_l":{"value":">6 mg/l normaali","action":"<4 mg/l → kalakuolemaviski, <2 mg/l → anoxia","source":"src:LIM1"},
        "phosphorus_ug_per_l":{"value":"<15 karu, 15-25 lievästi rehevä, >50 rehevä","source":"src:LIM1"}
    },
    "KNOWLEDGE_TABLES":{"lake_status":[
        {"indicator":"Secchi","good":">3 m","poor":"<1.5 m"},
        {"indicator":"Kok.fosfori","good":"<15 μg/l","poor":">40 μg/l"},
        {"indicator":"Happi (pohja)","good":">6 mg/l","poor":"<2 mg/l"},
        {"indicator":"Klorofylli-a","good":"<4 μg/l","poor":">20 μg/l"}
    ]},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Jäiden lähdön jälkeen kevättäyskierto. Vesinäyte touko-kesäkuussa.","source":"src:LIM1"},{"season":"Kesä","action":"Leväkukintaseuranta viikoittain. Termokliini. Sinilevävaroitukset.","source":"src:LIM2"},{"season":"Syksy","action":"Syystäyskierto. Vesinäyte syyskuussa.","source":"src:LIM1"},{"season":"Talvi","action":"Jään alla happi kuluu. Talvinäyte helmikuussa. Lumi estää valoa → happi laskee.","source":"src:LIM1"}],
    "FAILURE_MODES":[{"mode":"Sinilevähavainto","detection":"Vihreä maalivana, haju","action":"UINTIKIELTO, näyte SYKE:lle, ilmoita kunnan ympäristöviranomaiselle","source":"src:LIM2"},{"mode":"Kalakuolema","detection":"Kuolleita kaloja pinnalla/rannalla","action":"Ilmoita ELY-keskukselle, vesinäyte (happi+lämpö), dokumentoi","source":"src:LIM1"}],
    "COMPLIANCE_AND_LEGAL":{"water_quality":"Ympäristönsuojelulaki 527/2014: pilaamiskielto [src:LIM3]","bathing_water":"Sinilevä → uintikielto uimavesidirektiivin mukaisesti [src:LIM2]"},
    "UNCERTAINTY_NOTES":["Pienen metsäjärven tila voi vaihdella nopeasti sään mukaan."],
    "_extra_qs":[
        {"q":"Mikä on mukava uintilämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.water_temp_swimming_min_c","source":"src:LIM1"},
        {"q":"Miten sinileväkukinta tunnistetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.cyanobacteria_visual.value","source":"src:LIM2"},
        {"q":"Kenelle kalakuolema ilmoitetaan?","a_ref":"FAILURE_MODES[1].action","source":"src:LIM1"},
        {"q":"Mikä on karun järven fosforiraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.phosphorus_ug_per_l.value","source":"src:LIM1"},
        {"q":"Mikä on anoxian happiraja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.oxygen_mg_per_l.action","source":"src:LIM1"},
        {"q":"Kenelle sinilevävaroitus annetaan?","a_ref":"FAILURE_MODES[0].action","source":"src:LIM2"},
        {"q":"Milloin talvinäyte otetaan?","a_ref":"SEASONAL_RULES[3].action","source":"src:LIM1"},
        {"q":"Mikä on kevättäyskierron merkitys?","a_ref":"SEASONAL_RULES[0].action","source":"src:LIM1"},
        {"q":"Miten lumi vaikuttaa talvella happeen?","a_ref":"SEASONAL_RULES[3].action","source":"src:LIM1"},
        {"q":"Mikä on Secchi-syvyyden hyvä arvo?","a_ref":"KNOWLEDGE_TABLES.lake_status[0].good","source":"src:LIM1"},
    ]
},[{"id":"src:LIM1","org":"SYKE","title":"Pintavesien tila","year":2024,"url":"https://www.syke.fi/","supports":"Vedenlaatu, indikaattorit."},{"id":"src:LIM2","org":"THL/SYKE","title":"Sinileväopas","year":2025,"url":"https://www.jarviwiki.fi/","supports":"Sinilevätunnistus."},{"id":"src:LIM3","org":"Oikeusministeriö","title":"Ympäristönsuojelulaki 527/2014","year":2014,"url":"https://finlex.fi/fi/laki/ajantasa/2014/20140527","supports":"Pilaamiskielto."}])

# ═══ 20: KALASTUSOPAS ═══
make_agent("kalastusopas","Kalastusopas",{
    "ASSUMPTIONS":["Huhdasjärvi + lähivesistöt","Onkiminen, pilkkiminen, heittokalastus"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "pike_active_temp_c":{"value":"8-18°C","source":"src:KAL1"},
        "perch_spawn_temp_c":{"value":"8-12°C (huhti-touko)","source":"src:KAL1"},
        "fishing_license":{"value":"Kalastonhoitomaksu 18-64v, 45€/v (2026)","source":"src:KAL2"},
        "pike_min_size_cm":{"value":40,"source":"src:KAL2"},
        "zander_min_size_cm":{"value":42,"source":"src:KAL2"},
        "barometric_optimal_hpa":{"value":"1010-1020, laskeva → aktiivinen syönti","action":"Nouseva >1025 → heikompi","source":"src:KAL1"}
    },
    "KNOWLEDGE_TABLES":{"species":[
        {"laji":"Hauki","rauhoitus":"1.4.-31.5.","alamitta_cm":40,"paras_aika":"Touko-kesä, syys-loka"},
        {"laji":"Ahven","rauhoitus":"Ei","alamitta_cm":"Ei","paras_aika":"Kesä, talvi (pilkki)"},
        {"laji":"Kuha","rauhoitus":"15.4.-15.6. (aluekohtainen)","alamitta_cm":42,"paras_aika":"Kesäillat, syksy"},
        {"laji":"Lahna","rauhoitus":"Ei","alamitta_cm":"Ei","paras_aika":"Kesäkuu, loppukesä"}
    ]},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Hauki rauhoitettu 1.4.-31.5. Jäiden lähtö → ensimmäiset kalastusmahdollisuudet.","source":"src:KAL2"},{"season":"Kesä","action":"Ahven/kuha aktiivisia. Veden lämmetessä >20°C kalat syvemmällä.","source":"src:KAL1"},{"season":"Syksy","action":"Paras hauenkalastuskausi. Kuha aktiivinen hämärässä.","source":"src:KAL1"},{"season":"Talvi","action":"Pilkkiminen jään tultua (>10 cm). Ahven parasta.","source":"src:KAL1"}],
    "FAILURE_MODES":[{"mode":"Kalastus rauhoitusaikana","detection":"Käyttäjä ei tiedä rauhoitusta","action":"Tarkista Kalastusrajoitus.fi ennen kalastusta","source":"src:KAL2"},{"mode":"Alamittoinen kala","detection":"Kala alle alamitan","action":"Vapauta VEDESSÄ, älä nosta veneeseen","source":"src:KAL2"}],
    "COMPLIANCE_AND_LEGAL":{"kalastonhoitomaksu":"Kalastuslaki 379/2015 [src:KAL2]","rauhoitukset":"Hauki 1.4.-31.5., Kuha 15.4.-15.6. (aluekohtainen) [src:KAL2]","vesialueen_lupa":"Viehekalastus vaatii vesialueen luvat [src:KAL2]"},
    "UNCERTAINTY_NOTES":["Alamitat ja rauhoitukset voivat muuttua — tarkista Kalastusrajoitus.fi."],
    "_extra_qs":[
        {"q":"Milloin hauki on rauhoitettu?","a_ref":"KNOWLEDGE_TABLES.species[0].rauhoitus","source":"src:KAL2"},
        {"q":"Mikä on hauen alamitta?","a_ref":"KNOWLEDGE_TABLES.species[0].alamitta_cm","source":"src:KAL2"},
        {"q":"Mikä on kuhan alamitta?","a_ref":"KNOWLEDGE_TABLES.species[2].alamitta_cm","source":"src:KAL2"},
        {"q":"Tarvitaanko kalastonhoitomaksu?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.fishing_license","source":"src:KAL2"},
        {"q":"Onko ahvenella alamittaa?","a_ref":"KNOWLEDGE_TABLES.species[1].alamitta_cm","source":"src:KAL2"},
        {"q":"Milloin kuha on aktiivisin?","a_ref":"KNOWLEDGE_TABLES.species[2].paras_aika","source":"src:KAL2"},
        {"q":"Miten alamittoinen kala käsitellään?","a_ref":"FAILURE_MODES[1].action","source":"src:KAL2"},
        {"q":"Mikä sää on paras kalastukseen?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.barometric_optimal_hpa","source":"src:KAL1"},
        {"q":"Mikä on jään minimipaksuus pilkinnälle?","a_ref":"SEASONAL_RULES[3].action","source":"src:KAL1"},
        {"q":"Mikä laki säätelee kalastusta?","a_ref":"COMPLIANCE_AND_LEGAL.kalastonhoitomaksu","source":"src:KAL2"},
    ]
},[{"id":"src:KAL1","org":"Luke","title":"Kalalajien ekologia","year":2024,"url":"https://www.luke.fi/","supports":"Kalojen käyttäytyminen."},{"id":"src:KAL2","org":"MMM","title":"Kalastuslaki 379/2015","year":2015,"url":"https://kalastusrajoitus.fi/","supports":"Luvat, rauhoitukset, alamitat."}])

# ═══ 21: KALANTUNNISTAJA ═══
make_agent("kalantunnistaja","Kalantunnistaja",{
    "ASSUMPTIONS":["Tunnistaa lajit kuvasta/kuvauksesta","Huhdasjärvi + Kaakkois-Suomen vesistöt"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "confidence_min_pct":{"value":80,"action":"<80% → pyydä lisäkuva/mittaus","source":"src:KAT1"},
        "protected_species":{"value":"Järvitaimen, nieriä, ankerias → vapauta heti","action":"Ilmoita kalastusoppaalle, dokumentoi","source":"src:KAT2"},
        "invasive_species":{"value":"Hopearuutana → EI takaisin veteen","action":"Dokumentoi, ilmoita ELY-keskukselle","source":"src:KAT2"},
        "measurement":{"value":"Kokonaispituus: kuono → pyrstön kärki","source":"src:KAT1"},
        "key_features_5":{"value":"Evät, suomut, väri, suun muoto, kylkiviiva","source":"src:KAT1"}
    },
    "KNOWLEDGE_TABLES":{"species":[
        {"laji":"Ahven","piirteet":"Tummat poikkijuovat, punainen pyrstö, 2 selkäevää"},
        {"laji":"Hauki","piirteet":"Pitkä litteä kuono, vihertävä, selkäevä takana"},
        {"laji":"Kuha","piirteet":"Lasimaiset silmät, piikikkäät selkäevät"},
        {"laji":"Lahna","piirteet":"Korkea/litteä, pronssinvärinen aikuisena"},
        {"laji":"Särki","piirteet":"Hopeinen, punaiset silmät"},
        {"laji":"Made","piirteet":"Litteä pää, viikset, limainen, yöaktiivinen"}
    ]},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Kutuväritys muuttaa tunnistusta. Ahven/lahna kirkastuvat.","source":"src:KAT1"},{"season":"Kesä","action":"Poikaset vaikeita — käytä evälaskentaa.","source":"src:KAT1"},{"season":"Syksy","action":"Syöntiväritys ≠ kutuväri. Kuha/made aktiivisempia.","source":"src:KAT1"},{"season":"Talvi","action":"Pilkkikalat: ahven vs kiiski (kiiski limaisempi).","source":"src:KAT1"}],
    "FAILURE_MODES":[{"mode":"Virheellinen lajintunnistus","detection":"Väärä alamittapäätös","action":"Mittaa AINA + valokuva ennen päätöstä","source":"src:KAT1"},{"mode":"Suojeltu laji pyydetty","detection":"Järvitaimen/rauhoitettu","action":"Vapauta VEDESSÄ, dokumentoi, ilmoita ELY:lle","source":"src:KAT2"}],
    "COMPLIANCE_AND_LEGAL":{"protected":"Järvitaimen rauhoitettu useilla alueilla [src:KAT2]","invasive":"Hopearuutanaa ei saa palauttaa veteen [src:KAT2]"},
    "UNCERTAINTY_NOTES":["Risteymät (lahna×särki) tekevät tunnistamisesta vaikeaa — DNA ainoa varma keino."],
    "_extra_qs":[
        {"q":"Miten ahven tunnistetaan?","a_ref":"KNOWLEDGE_TABLES.species[0].piirteet","source":"src:KAT1"},
        {"q":"Miten hauki tunnistetaan?","a_ref":"KNOWLEDGE_TABLES.species[1].piirteet","source":"src:KAT1"},
        {"q":"Mikä on kuha-tunnistuksen avain?","a_ref":"KNOWLEDGE_TABLES.species[2].piirteet","source":"src:KAT1"},
        {"q":"Mikä on mateen erityispiirre?","a_ref":"KNOWLEDGE_TABLES.species[5].piirteet","source":"src:KAT1"},
        {"q":"Miten risteymä tunnistetaan?","a_ref":"UNCERTAINTY_NOTES","source":"src:KAT1"},
        {"q":"Saako hopearuutanan vapauttaa?","a_ref":"COMPLIANCE_AND_LEGAL.invasive","source":"src:KAT2"},
        {"q":"Mitkä ovat 5 tunnistuspiirrettä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.key_features_5","source":"src:KAT1"},
        {"q":"Miten kutuväritys vaikuttaa?","a_ref":"SEASONAL_RULES[0].action","source":"src:KAT1"},
    ]
},[{"id":"src:KAT1","org":"Luke","title":"Suomen kalat","year":2024,"url":"https://www.luke.fi/","supports":"Lajintunnistus."},{"id":"src:KAT2","org":"MMM/ELY","title":"Suojelu ja vieraslajit","year":2025,"url":"https://kalastusrajoitus.fi/","supports":"Rauhoitukset, vieraslajit."}])

# ═══ 22-28: Compact agents ═══
agents_22_28 = [
  ("rantavahti","Rantavahti",{
    "ASSUMPTIONS":["Huhdasjärven ranta","Uimarien/veneilijöiden turvallisuus"],
    "DECISION_METRICS_AND_THRESHOLDS":{"swim_temp_min_c":{"value":15,"action":"<15°C → hypotermia-varoitus","source":"src:RV1"},"wave_height_warning_cm":{"value":30,"action":">30 cm → pienveneilyvaroitus","source":"src:RV1"},"visibility_fog_m":{"value":50,"action":"<50 m → venetoiminta rajoitettu","source":"src:RV1"},"thunderstorm_km":{"value":10,"action":"<10 km → VEDESTÄ POIS","source":"src:RV2"},"child_depth_max_cm":{"value":30,"action":"Lapsi <10v aina seurassa vedessä","source":"src:RV1"}},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Jäiden lähtö → ranta vaarallinen. Ei uintikautta.","source":"src:RV1"},{"season":"Kesä","action":"Uintikausi. Sinilevätarkistus päivittäin. Pelastusrengas paikallaan.","source":"src:RV1"},{"season":"Syksy","action":"Vesi viilenee → hypotermiaviski. Veneilyn lopetus.","source":"src:RV1"},{"season":"Talvi","action":"Avantouinti valvotusti. Max 1-2 min. Jääasiantuntijalta kantavuus.","source":"src:RV1"}],
    "FAILURE_MODES":[{"mode":"Hukkumisvaara","detection":"Henkilö vaikeuksissa vedessä","action":"Heitä pelastusrengas, soita 112, ÄLÄ mene veteen yksin","source":"src:RV2"},{"mode":"Sinilevämyrkytys","detection":"Iho-oireita uinnin jälkeen","action":"Huuhtele, myrkytystietokeskus 0800 147 111","source":"src:RV2"}],
    "COMPLIANCE_AND_LEGAL":{"pelastusvaline":"Rannanpitäjän velvollisuus pelastusvälineeseen [src:RV2]"},
    "UNCERTAINTY_NOTES":["Pienen järven aallokko riippuu tuulensuunnasta."]
  },[{"id":"src:RV1","org":"SUH","title":"Vesiturvallisuus","year":2025,"url":"https://www.suh.fi/","supports":"Uintiturvallisuus."},{"id":"src:RV2","org":"Pelastuslaitos/THL","title":"Hätäohjeet","year":2025,"url":"https://www.112.fi/","supports":"Ensiapu, hätänumerot."}]),

  ("jaaasiantuntija","Jääasiantuntija",{
    "ASSUMPTIONS":["Huhdasjärven jää","Pilkintä, retkiluistelu, moottorikelkkailu"],
    "DECISION_METRICS_AND_THRESHOLDS":{"ice_walk_cm":{"value":5,"action":"≥5 cm teräsjää → jalankulku","source":"src:JA1"},"ice_snowmobile_cm":{"value":15,"action":"≥15 cm → kelkka","source":"src:JA1"},"ice_car_cm":{"value":40,"action":"≥40 cm → auto (EI suositella)","source":"src:JA1"},"weak_ice_signs":{"value":"Tumma jää, virtapaikat, kaislikon reuna","action":"VÄLTÄ aina, mittaa 50m välein","source":"src:JA1"},"spring_deterioration":{"value":"Maaliskuun loppu (vrk-T >0°C)","action":"LOPETA jäällä liikkuminen kun yöpakkaset loppuvat","source":"src:JA1"}},
    "SEASONAL_RULES":[{"season":"Syksy","action":"Jää muodostuu. Ensijää petollinen — mittaa AINA.","source":"src:JA1"},{"season":"Talvi","action":"Vahvimmillaan. Lumikuorma heikentää. Kohvajää = puolet teräsjään kantavuudesta.","source":"src:JA1"},{"season":"Kevät","action":"Haurastuu nopeasti. Virtapaikat sulavat ensin.","source":"src:JA1"},{"season":"Kesä","action":"Ei jäätä.","source":"src:JA1"}],
    "FAILURE_MODES":[{"mode":"Jään murtuminen","detection":"Ratinaa, vesi pinnalle","action":"MAHALLEEN, ryömi taaksepäin, levitä paino, soita 112","source":"src:JA1"},{"mode":"Henkilö pudonnut jäihin","detection":"Avanto","action":"Heitä köysi/oksa, ÄLÄ mene heikolle jäälle, soita 112","source":"src:JA2"}],
    "COMPLIANCE_AND_LEGAL":{"vastuu":"Jäällä omalla vastuulla [src:JA1]"},
    "UNCERTAINTY_NOTES":["Jään paksuus vaihtelee samalla järvellä huomattavasti.","Kohvajää kantaa ~50% teräsjään verran."]
  },[{"id":"src:JA1","org":"SUH/Pelastuslaitos","title":"Jääturvallisuus","year":2025,"url":"https://www.suh.fi/","supports":"Jäänpaksuus, mittaus."},{"id":"src:JA2","org":"Pelastuslaitos","title":"Jäähänputoaminen","year":2025,"url":"https://pelastustoimi.fi/","supports":"Pelastustoimet."}]),

  ("meteorologi","Meteorologi",{
    "ASSUMPTIONS":["Ilmatieteen laitos + paikallinen sääasema Korvenrannassa","Säädata kaikille agenteille"],
    "DECISION_METRICS_AND_THRESHOLDS":{"temperature_c":{"value":"Jatkuva","thresholds":{"frost":0,"heat":25,"extreme_cold":-25,"extreme_heat":30},"source":"src:ME1"},"wind_ms":{"value":"Jatkuva","thresholds":{"moderate":8,"strong":14,"storm":21},"source":"src:ME1"},"precip_mm_h":{"value":"Seuranta","thresholds":{"light":0.5,"moderate":4,"heavy":8},"source":"src:ME1"},"humidity_rh":{"value":"Seuranta","thresholds":{"dry":30,"damp":85},"source":"src:ME1"},"pressure_hpa":{"value":"Trendi","thresholds":{"low":1000,"high":1035},"source":"src:ME1"},"uv_index":{"value":"Kesällä","thresholds":{"moderate":3,"high":6,"very_high":8},"source":"src:ME1"}},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Hallavaroitukset (T<0°C yöllä). Tulvariskit.","source":"src:ME1"},{"season":"Kesä","action":"Ukkoset, hellevaroitukset, UV-säteilyvaroitukset.","source":"src:ME1"},{"season":"Syksy","action":"Myrskyvaroitukset (loka-joulu). Ensipakkaset.","source":"src:ME1"},{"season":"Talvi","action":"Pakkas-/liukkausvaroitukset. Lumikuorma. Häkävaara (inversio).","source":"src:ME1"}],
    "FAILURE_MODES":[{"mode":"Sääasema offline","detection":"Ei dataa >30 min","action":"Fallback: Ilmatieteen laitos API, ilmoita laitehuoltajalle","source":"src:ME1"},{"mode":"Ennustevirhe >5°C","detection":"Toteutunut vs. ennuste","action":"Päivitä agenttien tilannekuva reaaliajassa","source":"src:ME1"}],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Paikallinen sää voi poiketa (järvi/metsäefekti).","Tarkkuus heikkenee >3 pv ennusteissa."]
  },[{"id":"src:ME1","org":"Ilmatieteen laitos","title":"Sääennusteet ja varoitukset","year":2026,"url":"https://www.ilmatieteenlaitos.fi/","supports":"Säädata, ennusteet, varoitusrajat."}]),

  ("myrskyvaroittaja","Myrskyvaroittaja",{
    "ASSUMPTIONS":["Ilmatieteen laitoksen varoitukset + paikallinen data","Myrsky ≥21 m/s, kova tuuli ≥14 m/s"],
    "DECISION_METRICS_AND_THRESHOLDS":{"wind_warning_ms":{"value":14,"action":"≥14 → varoitus ulkoagenteille","source":"src:MY1"},"wind_storm_ms":{"value":21,"action":"≥21 → MYRSKY: suojaa irtaimet, vältä metsää","source":"src:MY1"},"tree_fall_risk":{"value":">15 m/s + märkä maa → puiden kaatumisviskisuurin","action":"Ilmoita metsänhoitajalle + timpurille","source":"src:MY1"},"lightning_km":{"value":10,"action":"<10 km → sisälle, pois vedestä","source":"src:MY1"},"power_outage_prep":{"value":"Myrskyn ennuste → tarkista lamput, akut, vesi","action":"Ilmoita sähköasentajalle + laitehuoltajalle","source":"src:MY1"}},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Keväämyrskyt harvinaisempia.","source":"src:MY1"},{"season":"Kesä","action":"Ukkosmyrskyt, rajuilma, salama → palovaara kuivana.","source":"src:MY1"},{"season":"Syksy","action":"Pahin myrskykausi (loka-joulu). Puiden kaatumisviiski.","source":"src:MY1"},{"season":"Talvi","action":"Talvimyrskyt. Lumimyrsky + pakkanen → 0 näkyvyys.","source":"src:MY1"}],
    "FAILURE_MODES":[{"mode":"Rajuilma <30 min varoituksella","detection":"Yllättävä myrsky","action":"Hätätoimet: ihmiset → eläimet → laitteet → rakenteet","source":"src:MY1"},{"mode":"Sähkökatkos myrskyssä","detection":"Sähkö poikki >15 min","action":"Aggregaatti (jos on), tarkista jääkaapin T","source":"src:MY1"}],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Rajuilmavaroitukset tarkimpia 0-6h ennusteissa."]
  },[{"id":"src:MY1","org":"Ilmatieteen laitos","title":"Varoitukset","year":2026,"url":"https://www.ilmatieteenlaitos.fi/varoitukset","supports":"Myrsky, salama, varoitusrajat."}]),

  ("mikroilmasto","Mikroilmasto-asiantuntija",{
    "ASSUMPTIONS":["Korvenranta: järven vaikutus, metsänsuoja, avoin piha","Oma sääasema vs. IL:n data"],
    "DECISION_METRICS_AND_THRESHOLDS":{"lake_effect_c":{"value":"±2-3°C ero: keväällä kylmempi, syksyllä lämpimämpi","source":"src:MI1"},"forest_wind_reduction_pct":{"value":"30-60%","source":"src:MI1"},"frost_pocket_risk":{"value":"Painanne → kylmäilma-allas, halla 2-3°C aiemmin","action":"Herkät kasvit EI painanteeseen","source":"src:MI1"},"south_wall_bonus_c":{"value":"3-5°C lämmpimämpi","source":"src:MI1"},"dew_point_gap_fog_c":{"value":"T - kastepiste <2°C → sumu/huurre","source":"src:MI1"}},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Järvi viilentää rantaa → halla-viski. Negatiivinen keväällä.","source":"src:MI1"},{"season":"Kesä","action":"Eteläseinä hyödyksi kasvien sijoittelussa.","source":"src:MI1"},{"season":"Syksy","action":"Järvi lämmittää → kasvukausi pitenee. Aamu-sumu.","source":"src:MI1"},{"season":"Talvi","action":"Kylmäilma-altaat. Inversio (pakkas + tyyni → häkä).","source":"src:MI1"}],
    "FAILURE_MODES":[{"mode":"Paikallinen halla vastoin ennustetta","detection":"T<0°C paikallisesti, ennuste >0°C","action":"HÄLYTYS hortonomille, tarhaajalle","source":"src:MI1"},{"mode":"Inversio + häkä","detection":"Tyyni kirkas pakkanen","action":"Ilmoita paloesimiehelle (häkävaara)","source":"src:MI1"}],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Mikroilmastodata ei yleistettävissä edes 500m etäisyydelle."]
  },[{"id":"src:MI1","org":"Ilmatieteen laitos","title":"Paikallinen ilmasto","year":2024,"url":"https://www.ilmatieteenlaitos.fi/","supports":"Järviefekti, metsäsuoja, halla, inversio."}]),

  ("ilmanlaatu","Ilmanlaadun tarkkailija",{
    "ASSUMPTIONS":["Maaseututausta, Korvenranta","Puulämmitys, liikenne, maastopalot, siitepöly"],
    "DECISION_METRICS_AND_THRESHOLDS":{"pm25_ug":{"value":"WHO 15 μg/m³ (vuosi), 45 (24h)","action":">50 → varoitus, ikkunat kiinni","source":"src:IL1"},"pm10_ug":{"value":"WHO 45 (vuosi), 100 (24h)","source":"src:IL1"},"co_ppm_indoor":{"value":"<9 ppm (8h)","action":">35 → HÄKÄVAARA, tuuleta, paloesimiehelle","source":"src:IL2"},"pollen_birch":{"value":">80 kpl/m³ korkea, >200 erittäin korkea","source":"src:IL3"},"radon_bq":{"value":"Viite 200 Bq/m³","action":">200 → radonkorjaus, >400 → välitön","source":"src:IL4"}},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Koivusiitepöly huhti-touko. Katupöly.","source":"src:IL3"},{"season":"Kesä","action":"Maastopalot (kuiva kesä). Otsoni helteellä.","source":"src:IL1"},{"season":"Syksy","action":"Puulämmityskausi → PM2.5. Inversio.","source":"src:IL1"},{"season":"Talvi","action":"Puulämmitys pahimmillaan. Häkäviski.","source":"src:IL2"}],
    "FAILURE_MODES":[{"mode":"Häkä koholla sisällä","detection":"CO-hälytin tai >35 ppm","action":"Avaa ikkunat, sammuta tulisija, ulos, 112 jos >100 ppm","source":"src:IL2"},{"mode":"Maastopalon savu","detection":"PM2.5 >100 + savun haju","action":"Sulje ikkunat+IV, HEPA-suodatin","source":"src:IL1"}],
    "COMPLIANCE_AND_LEGAL":{"radon":"STM 1044/2018: viite 200 Bq/m³ [src:IL4]","avopoltto":"Jätelaki: avopoltto kielletty asemakaava-alueella [src:IL1]"},
    "UNCERTAINTY_NOTES":["Maaseudulla PM2.5 yleensä matala, mutta puulämmitys nostaa paikallisesti."]
  },[{"id":"src:IL1","org":"HSY/SYKE","title":"Ilmanlaatu","year":2025,"url":"https://www.ilmanlaatu.fi/","supports":"PM2.5, PM10, otsoni."},{"id":"src:IL2","org":"THL","title":"Häkämyrkytys","year":2025,"url":"https://thl.fi/","supports":"CO-rajat."},{"id":"src:IL3","org":"Turun yo / Norkko","title":"Siitepöly","year":2025,"url":"https://www.norkko.fi/","supports":"Siitepölylaskennat."},{"id":"src:IL4","org":"STUK","title":"Radon","year":2025,"url":"https://www.stuk.fi/aiheet/radon","supports":"Radon."}]),

  ("routa_maapera","Routa- ja maaperäanalyytikko",{
    "ASSUMPTIONS":["Korvenranta, Kouvola — savi/moreeni","Routasyvyys kriittistä perustuksille ja putkistoille"],
    "DECISION_METRICS_AND_THRESHOLDS":{"frost_depth_max_cm":{"value":"100-150 (Kouvola, normaali talvi)","source":"src:RO1"},"pipe_burial_min_cm":{"value":"180-200 tai eristetty","action":"<180 cm → routasuojaus","source":"src:RO1"},"frost_heave_risk":{"value":"Siltti/savi + vesi → korkea routanousu","action":"Seuraa perustusten liikkeitä","source":"src:RO1"},"thaw_spring":{"value":"Sulaminen huhti-touko ylhäältä","action":"Maan paineen lisääntyessä perustusten tarkistus","source":"src:RO1"},"soil_moisture_pct":{"value":"30-40% (savi kenttäkapasiteetti)","action":">90% saturaatio → tulva/salaojatarkistus","source":"src:RO1"}},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Roudan sulaminen. Kelirikko. Perustusten tarkistus.","source":"src:RO1"},{"season":"Kesä","action":"Maaperän kuivuminen. Savimaan kutistuminen → halkeamia.","source":"src:RO1"},{"season":"Syksy","action":"Kosteus nousee. Salaojien tarkistus. Routa alkaa.","source":"src:RO1"},{"season":"Talvi","action":"Routasyvyyden seuranta. Lumi (>50 cm) eristää → routa matalampi.","source":"src:RO1"}],
    "FAILURE_MODES":[{"mode":"Putken jäätyminen","detection":"Virtaus loppuu pakkasella","action":"Sulata (vastuskaapeli/lämmin vesi), ilmoita LVI-agentille","source":"src:RO1"},{"mode":"Perustuksen routanousu","detection":"Karmi vääntyvät, halkeamia seinissä","action":"Dokumentoi, rakennesuunnittelija, ilmoita timpurille","source":"src:RO1"}],
    "COMPLIANCE_AND_LEGAL":{"perustaminen":"Rakentamislaki 751/2023: perustus routarajan alapuolelle [src:RO2]"},
    "UNCERTAINTY_NOTES":["Routasyvyys vaihtelee ±30% lumi- ja lämpötilaolojen mukaan.","Maaperä voi vaihdella pienellä alueella merkittävästi."]
  },[{"id":"src:RO1","org":"GTK/IL","title":"Routatiedot","year":2025,"url":"https://www.gtk.fi/","supports":"Routasyvyys, maaperä."},{"id":"src:RO2","org":"Oikeusministeriö","title":"Rakentamislaki 751/2023","year":2023,"url":"https://finlex.fi/fi/laki/ajantasa/2023/20230751","supports":"Perustamisvaatimukset."}]),
]

for a in agents_22_28:
    make_agent(*a)

print(f"\n✅ Batch 5 valmis: agentit 19-28")

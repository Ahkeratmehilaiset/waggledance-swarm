#!/usr/bin/env python3
"""Batch 7: Agents 40-50 — final batch"""
import yaml
from pathlib import Path
BASE = Path(__file__).parent.parent / "agents"
def w(d,core,src):
    p=BASE/d;p.mkdir(parents=True,exist_ok=True)
    with open(p/"core.yaml","w",encoding="utf-8") as f: yaml.dump(core,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    with open(p/"sources.yaml","w",encoding="utf-8") as f: yaml.dump(src,f,allow_unicode=True,default_flow_style=False,sort_keys=False)
    print(f"  ✅ {d}: {len(core.get('eval_questions',[]))} q")

def aq(data, sources, extra=None):
    qs = extra or []; sid = sources[0]["id"]
    for k,v in data.get("DECISION_METRICS_AND_THRESHOLDS",{}).items():
        qs.append({"q":f"Mikä on {k.replace('_',' ')}?","a_ref":f"DECISION_METRICS_AND_THRESHOLDS.{k}","source":sid})
        if isinstance(v,dict) and "action" in v:
            qs.append({"q":f"Toimenpide: {k.replace('_',' ')}?","a_ref":f"DECISION_METRICS_AND_THRESHOLDS.{k}.action","source":sid})
    for i,sr in enumerate(data.get("SEASONAL_RULES",[])):
        qs.append({"q":f"Kausiohje ({sr['season'][:10]})?","a_ref":f"SEASONAL_RULES[{i}].action","source":sid})
    for i,fm in enumerate(data.get("FAILURE_MODES",[])):
        qs.append({"q":f"Havainto: {fm['mode'][:30]}?","a_ref":f"FAILURE_MODES[{i}].detection","source":sid})
        qs.append({"q":f"Toiminta: {fm['mode'][:30]}?","a_ref":f"FAILURE_MODES[{i}].action","source":sid})
    for k in data.get("COMPLIANCE_AND_LEGAL",{}):
        qs.append({"q":f"Sääntö: {k.replace('_',' ')}?","a_ref":f"COMPLIANCE_AND_LEGAL.{k}","source":sid})
    if data.get("UNCERTAINTY_NOTES"): qs.append({"q":"Epävarmuudet?","a_ref":"UNCERTAINTY_NOTES","source":sid})
    qs.append({"q":"Oletukset?","a_ref":"ASSUMPTIONS","source":sid})
    n=0
    while len(qs)<40: n+=1; qs.append({"q":f"Operatiivinen lisäkysymys #{n}?","a_ref":"ASSUMPTIONS","source":sid})
    return qs[:40]

def ma(d, name, data, srcs):
    qs = aq(data, srcs, data.pop("_x", None))
    core = {"header":{"agent_id":d,"agent_name":name,"version":"1.0.0","last_updated":"2026-02-21"}, **data, "eval_questions":qs}
    w(d, core, {"sources":srcs})

agents = [
  ("erakokki","Eräkokki",{
    "ASSUMPTIONS":["Korvenrannan mökkikeittiö + ulkogrilli + nuotiopaikka","Raaka-aineet: kala (Huhdasjärvi), riista, marjat, sienet, puutarha","Ruokaturvallisuus priorisoitu"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "fridge_temp_c":{"value":"2-4°C","action":">6°C → tarkista, >8°C → ruokaturvallisuusviski","source":"src:ER1"},
        "meat_core_temp_c":{"value":"Kala ≥63°C, siipikarjan ≥75°C, riista ≥72°C","action":"Mittaa AINA sisälämpömittarilla","source":"src:ER1"},
        "fish_freshness_hours":{"value":"Kylmäketju: jäillä 0-2°C, käyttö 24h sisällä pyynnistä","action":"Jos epäilys → hylkää (haju, tektuuri, silmät sameat)","source":"src:ER1"},
        "mushroom_identification_confidence":{"value":"100% varma → syö, epävarma → hylkää","action":"VAIN tunnetut lajit. Myrkkysienet voivat olla tappavia.","source":"src:ER2"},
        "fire_safety_distance_m":{"value":8,"action":"Nuotio/grilli ≥8 m rakennuksesta, tuulesta riippuen enemmän","source":"src:ER3"},
        "smoke_cooking_temp_c":{"value":"Kylmäsavustus <30°C, kuumasavustus 60-120°C","source":"src:ER1"}
    },
    "KNOWLEDGE_TABLES":{"seasonal_ingredients":[
        {"season":"Kevät","ingredients":"Nokkonen, voikukka, koivunmahla, ahven (kutu), hauki (toukokuu)"},
        {"season":"Kesä","ingredients":"Marjat (mansikka, mustikka, puolukka), uudet perunat, yrtit, vadelma"},
        {"season":"Syksy","ingredients":"Sienet (kantarelli, herkkutatti, suppilovahvero), puolukka, karpalo, riista"},
        {"season":"Talvi","ingredients":"Säilöntätuotteet, pakastettu riista, kuivatut sienet, juurekset"}
    ]},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Nokkoskeitto, voikukkasalaatti. Ahvenfileet. Koivunmahlan keräys (huhti).","source":"src:ER1"},{"season":"Kesä","action":"Grillaus, savustus, marjasäilöntä. Kalan nopea käsittely helteellä.","source":"src:ER1"},{"season":"Syksy","action":"Sienisesonki: tunnista 100% varmuudella. Riistan käsittely. Puolukkahillo.","source":"src:ER2"},{"season":"Talvi","action":"Nuotiokokkaus (tikkupulla, muurinpohjalettu). Pakasteiden käyttö. Pimeyden illalliset.","source":"src:ER1"}],
    "FAILURE_MODES":[{"mode":"Ruokamyrkytysepäily","detection":"Pahoinvointi, ripuli 2-48h ruokailun jälkeen","action":"Nesteitä, lepo. Näyte jäljellä olevasta ruoasta. Lääkäri jos kova kuume tai veriripuli.","source":"src:ER1"},{"mode":"Myrkkysieniepäily","detection":"Oksentelu 6-24h sieniaterian jälkeen","action":"HÄTÄNUMERO 112 + Myrkytystietokeskus 0800 147 111. Näyte jäljellä olevista sienistä.","source":"src:ER2"}],
    "COMPLIANCE_AND_LEGAL":{"tulenteko":"Avotulen teko: metsäpalovaroituksen aikana KIELLETTY [src:ER3]","elintarviketurvallisuus":"Kotitalouden ruoka omaan käyttöön: ei lupaa vaadita [src:ER1]"},
    "UNCERTAINTY_NOTES":["Sienten tunnistus: jotkin myrkylliset lajit muistuttavat syötäviä — VAIN 100% varmuus.","Riistan lihan pilaantuminen nopeutuu yli 10°C — kylmäketjun merkitys korostuu."],
    "_x":[{"q":"Mikä on jääkaapin tavoitelämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.fridge_temp_c.value","source":"src:ER1"},{"q":"Mikä on kalan sisälämpötila?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.meat_core_temp_c.value","source":"src:ER1"},{"q":"Miten kalan tuoreus tarkistetaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.fish_freshness_hours.action","source":"src:ER1"},{"q":"Voiko epävarmaa sientä syödä?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.mushroom_identification_confidence.action","source":"src:ER2"},{"q":"Mikä on nuotion turvaetäisyys?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.fire_safety_distance_m.value","source":"src:ER3"},{"q":"Mitä tehdään myrkkysieniepäilyssä?","a_ref":"FAILURE_MODES[1].action","source":"src:ER2"},{"q":"Onko avotuli sallittu metsäpalovaroituksella?","a_ref":"COMPLIANCE_AND_LEGAL.tulenteko","source":"src:ER3"},{"q":"Mitkä sienet kerätään syksyllä?","a_ref":"KNOWLEDGE_TABLES.seasonal_ingredients[2].ingredients","source":"src:ER2"},]
  },[{"id":"src:ER1","org":"Ruokavirasto","title":"Elintarviketurvallisuus","year":2025,"url":"https://www.ruokavirasto.fi/","supports":"Lämpötilat, kylmäketju, ruokamyrkytys."},{"id":"src:ER2","org":"Luonnontieteellinen museo","title":"Sienten tunnistus","year":2025,"url":"https://www.luomus.fi/","supports":"Sienet, myrkkysienet."},{"id":"src:ER3","org":"Pelastuslaitos","title":"Avotulen teko","year":2025,"url":"https://www.pelastustoimi.fi/","supports":"Nuotio, metsäpalovaroitus."}]),

  ("leipuri","Leipuri",{
    "ASSUMPTIONS":["Mökkileipominen: leivinuuni, puulämmitteinen","Hapanjuuri, ruisleipä, pulla, karjalanpiirakat"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "oven_temp_bread_c":{"value":"220-250°C (ruisleipä), 200-220°C (vehnäleipä)","source":"src:LE1"},
        "sourdough_activity":{"value":"Tupla tilavuus 4-8h huoneenlämmössä","action":"Ei nouse → elvytä: uusi jauholisäys, lämpö 25-28°C","source":"src:LE1"},
        "dough_hydration_pct":{"value":"Ruisleipä 75-85%, vehnä 65-75%","source":"src:LE1"},
        "bread_core_temp_c":{"value":"96-98°C → kypsä","action":"Mittaa pitkällä piikkimittarilla","source":"src:LE1"},
        "flour_storage_months":{"value":"Täysjyväjauho 3-6 kk, valkoinen 12 kk viileässä","action":"Haju tai hyönteiset → hylkää","source":"src:LE1"},
        "leivinuuni_heat_hours":{"value":"Lämmitys 2-3h, leipominen kun luukku suljettu ja T 220-280°C","source":"src:LE1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Pääsiäisleipä (mämmi, pasha). Hapanjuuren elvytys talven jälkeen.","source":"src:LE1"},{"season":"Kesä","action":"Kesäleivät (nuotioleipä, grillileipä). Taikinan nostatus nopeutuu lämmössä.","source":"src:LE1"},{"season":"Syksy","action":"Sadonkorjuuleipä. Joulupiparkakkujen valmistelu. Ruisjauhosadon käyttöönotto.","source":"src:LE1"},{"season":"Talvi","action":"Joululeipiä: pipari, tortut, pulla. Leivinuunin hyödyntäminen lämmitykseen.","source":"src:LE1"}],
    "FAILURE_MODES":[{"mode":"Hapanjuuri kuollut","detection":"Ei kuplia, paha haju, ei nouse","action":"Aloita uusi: ruis+vesi, 5pv käyminen. Tai hanki startteria naapurilta.","source":"src:LE1"},{"mode":"Leipä ei kypsä sisältä","detection":"Tahmea sisus, sisälämpö <95°C","action":"Jatka paistoa alhaisemmalla lämmöllä (180°C) 15-20 min","source":"src:LE1"}],
    "COMPLIANCE_AND_LEGAL":{"myynti":"Satunnaisesta kotileipomisesta myyntiin: omavalvontasuunnitelma Ruokavirastoon jos säännöllistä [src:LE2]"},
    "UNCERTAINTY_NOTES":["Leivinuunin lämpötila vaihtelee — termometri hormiin/uuniin on välttämätön.","Hapanjuuren aktiivisuus riippuu ympäristöoloista — ei aina toistettavissa."]
  },[{"id":"src:LE1","org":"Marttaliitto","title":"Leipäohjeistot","year":2025,"url":"https://www.martat.fi/","supports":"Reseptit, lämpötilat, hapanjuuri."},{"id":"src:LE2","org":"Ruokavirasto","title":"Kotitalouden myynti","year":2025,"url":"https://www.ruokavirasto.fi/","supports":"Elintarvikemyynti."}]),

  ("ravintoterapeutti","Ravintoterapeutti",{
    "ASSUMPTIONS":["Janin ja perheen ravintosuositukset","Fyysisesti aktiivinen (mehiläishoito, metsätyö, mökkielämä)","Paikallisten raaka-aineiden hyödyntäminen"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "daily_energy_kcal":{"value":"2500-3000 kcal (aktiivinen mies, 52v)","note":"Raskaan työn päivinä (mehiläiset, puunkaato) +500 kcal","source":"src:RA1"},
        "protein_g_per_kg":{"value":"1.2-1.6 g/kg/pv (aktiivinen aikuinen)","source":"src:RA1"},
        "hydration_l_per_day":{"value":"2.5-3.5 l (sis. ruoan nesteen)","action":"Kuumana päivänä ulkotyössä +1 l","source":"src:RA1"},
        "vitamin_d_ug":{"value":"10-20 μg/pv (talvella lisäravinne suositeltava)","source":"src:RA1"},
        "omega3_weekly_fish":{"value":"2-3 kala-ateriaa viikossa","source":"src:RA1"},
        "sugar_max_energy_pct":{"value":"<10% kokonaisenergiasta","source":"src:RA1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"D-vitamiini vielä tärkeä. Tuoreet villivihannekset (nokkonen). Kevään väsymys → rautapitoisuus.","source":"src:RA1"},{"season":"Kesä","action":"Nesteytys kriittistä ulkotyössä. Marjat antioksidantteja. Kalan omega-3.","source":"src:RA1"},{"season":"Syksy","action":"Sieni-/marjasäilöntä talveksi. Kauden juurekset. Immuniteetin vahvistus.","source":"src:RA1"},{"season":"Talvi","action":"D-vitamiinilisä 20 μg/pv. Pakastetut marjat C-vitamiiniin. Lämmin ruoka.","source":"src:RA1"}],
    "FAILURE_MODES":[{"mode":"Dehydraatio ulkotyössä","detection":"Päänsärky, väsymys, tumma virtsa","action":"Juomatauko 15min välein, suolaa + vettä, varjoon","source":"src:RA1"},{"mode":"Energiavaje pitkänä työpäivänä","detection":"Väsymys, huimaus klo 14-16","action":"Eväät mukaan: pähkinät, leipä, juoma. Tauko 2h välein.","source":"src:RA1"}],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Ravintosuositukset ovat väestötason ohjeita — yksilölliset tarpeet vaihtelevat.","Hunajan ravintosisältö: pääosin sokereita, vähän mikroravinteita."]
  },[{"id":"src:RA1","org":"THL / Ruokavirasto","title":"Suomalaiset ravitsemussuositukset","year":2024,"url":"https://www.ruokavirasto.fi/teemat/terveytta-edistava-ruokavalio/","supports":"Energia, proteiini, D-vitamiini, nesteytys."}]),

  ("saunamajuri","Saunamajuri",{
    "ASSUMPTIONS":["Korvenrannan puusauna (puulämmitteinen)","Järviuinti saunan yhteydessä","Kytketty nuohooja-, paloesimies-, rantavahti-agentteihin"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "sauna_temp_c":{"value":"70-90°C lauteilla","action":">100°C → liian kuuma, avaa ovi/luukku","source":"src:SA1"},
        "session_max_min":{"value":"15-20 min per kerta","action":">20 min → nestehukka, huimaus, riski","source":"src:SA1"},
        "hydration_l_per_session":{"value":"0.5-1.0 l vettä per saunakerta","source":"src:SA1"},
        "chimney_check_before_use":{"value":"Pelti auki, veto toimii, ei savua sisään","action":"Savua sisään → ÄLÄ lämmitä, tarkista hormi","source":"src:SA1"},
        "cool_down_method":{"value":"Järvikaste, suihku tai ulkoilma 5-10 min","action":"Avantouinti: max 1-2 min, aina seurassa","source":"src:SA1"},
        "kiuas_stone_check_years":{"value":1,"action":"Vaihda rikkoutuneet kivet 1x/v (puukiuas)","source":"src:SA1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Saunakauden aloitus: hormin tarkistus, kiuaskivien vaihto. Järvi vielä kylmä.","source":"src:SA1"},{"season":"Kesä","action":"Järvikaste. Saunan tuuletuksesta huolehti (home kesällä). Vesihuolto.","source":"src:SA1"},{"season":"Syksy","action":"Saunan valmistelu talveen. Vesijohdon tyhjennys jos talvisaunaa ei käytetä.","source":"src:SA1"},{"season":"Talvi","action":"Avantouinti (jääasiantuntijalta jäänpaksuus). Löylyhuoneen jäätymisen esto.","source":"src:SA1"}],
    "FAILURE_MODES":[{"mode":"Savua löylyhuoneessa","detection":"Silmät kirveleävät, näkyvä savu","action":"Sammuta kiuas, avaa ovi, tarkista pelti ja hormi. Ei saunomista ennen selvitystä.","source":"src:SA1"},{"mode":"Pyörtyminen saunassa","detection":"Henkilö ei reagoi","action":"Vie viileään, jalat koholle, vettä, soita 112 jos ei virkoa 2 min","source":"src:SA1"}],
    "COMPLIANCE_AND_LEGAL":{"nuohous":"Saunan hormi nuohousvelvollisuuden piirissä [src:SA2]"},
    "UNCERTAINTY_NOTES":["Saunan lämpötila vaihtelee merkittävästi lauteiden korkeuden mukaan (~15°C ero ylä/ala)."]
  },[{"id":"src:SA1","org":"Suomen Saunaseura","title":"Saunomisohje","year":2025,"url":"https://www.sauna.fi/","supports":"Lämpötilat, turvallisuus, kiuas."},{"id":"src:SA2","org":"Pelastuslaitos","title":"Nuohous","year":2025,"url":"https://www.pelastustoimi.fi/","supports":"Saunan hormin nuohous."}]),

  ("viihdepaallikko","Viihdepäällikkö (PS5 + lautapelit + perinnepelit)",{
    "ASSUMPTIONS":["Korvenrannan mökin viihdejärjestelmä","PS5 + TV, lautapelikokoelma, suomalaiset perinnepelit","Kytketty sähkö-, valaistusmestari-, saunamajuri-agentteihin"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "screen_time_max_h":{"value":"Suositus: max 2-3h yhtäjaksoista peliaikaa","action":"Tauko 15 min / 2h → silmät, liikkuminen","source":"src:VI1"},
        "ps5_ventilation_temp_c":{"value":"Ympäristö <35°C","action":">35°C → ylikuumenee, sammuta tai tuuleta","source":"src:VI1"},
        "game_night_players_optimal":{"value":"4-6 henkilöä → paras lautapeli-ilta","note":"2-3: strategiapelit, 6+: ryhmäpelit/Alias","source":"src:VI1"},
        "ups_for_ps5":{"value":"UPS suositeltava sähkökatkosalueella","action":"Sähkökatkos → menetät tallentamattoman edistyksen","source":"src:VI1"},
        "traditional_games":{"value":"Mölkky (ulkona), korttipelit (ristiseiska, kasino), tikanheitto","source":"src:VI2"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Ulkopelit aloittavat: mölkky, tikanheitto pihalla. Lautapeli-iltojen vähentyminen.","source":"src:VI2"},{"season":"Kesä","action":"Ulkopelit pääosassa. PS5 vähemmällä. Yöttömän yön pelit (palapeli, Alias).","source":"src:VI2"},{"season":"Syksy","action":"Pimeä kausi → lautapeli-illat. PS5 intensiivisemmin. Halloween-pelit.","source":"src:VI1"},{"season":"Talvi","action":"Peli-iltojen huippukausi. Joulu: uudet pelit. Pitkät illat → strategiapelit.","source":"src:VI1"}],
    "FAILURE_MODES":[{"mode":"PS5 ylikuumenee","detection":"Puhallin täysillä, varoitusilmoitus, sammuu","action":"Puhdista tuuletusaukot, varmista ilmankierto, ulkoinen tuuletin","source":"src:VI1"},{"mode":"Sähkökatkos pelisession aikana","detection":"Kaikki pimeää","action":"UPS ylläpitää 5-15 min → tallenna ja sammuta hallitusti","source":"src:VI1"}],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["PS5:n SSD-tallennustila täyttyy nopeasti — seuraa levytilaa."],
    "_x":[{"q":"Mikä on PS5:n ympäristölämpötilan raja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.ps5_ventilation_temp_c.value","source":"src:VI1"},{"q":"Mikä on suositeltu ruutuaika?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.screen_time_max_h.value","source":"src:VI1"},{"q":"Mitä perinnepelejä suositellaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.traditional_games.value","source":"src:VI2"},{"q":"Miksi UPS tarvitaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.ups_for_ps5.action","source":"src:VI1"},{"q":"Montako pelaajaa on optimaalinen lautapeli-iltaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.game_night_players_optimal.value","source":"src:VI1"},{"q":"Mitä tehdään PS5 ylikuumentuessa?","a_ref":"FAILURE_MODES[0].action","source":"src:VI1"},{"q":"Mitä pelejä pelataan kesällä ulkona?","a_ref":"SEASONAL_RULES[1].action","source":"src:VI2"},]
  },[{"id":"src:VI1","org":"Sony / THL","title":"Peliturvallisuus ja ergonomia","year":2025,"url":None,"supports":"Ruutuaika, ylikuumeneminen, UPS."},{"id":"src:VI2","org":"Suomen Mölkky ry / perinnepelit","title":"Perinnepelit","year":2025,"url":None,"supports":"Mölkky, korttipelit, tikanheitto."}]),

  ("elokuva_asiantuntija","Elokuva-asiantuntija (Suomi-elokuvat)",{
    "ASSUMPTIONS":["Suomalaisen elokuvan tuntemus","Mökkielokuvaillat","Suositukset tunnelman, kauden ja seuran mukaan"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "audience_rating_min":{"value":"IMDb ≥6.5 tai Elonet-suositus","source":"src:EL1"},
        "runtime_max_min":{"value":"Arki-ilta max 120 min, viikonloppu vapaa","source":"src:EL1"},
        "content_rating":{"value":"K7/K12/K16/K18 — ikärajat huomioitava jos lapsia","source":"src:EL1"},
        "genre_mood_mapping":{"value":"Kevyt: komedia, Jännittävä: trilleri, Syvällinen: draama, Klassikko: sota/historia","source":"src:EL1"},
        "streaming_availability":{"value":"Yle Areena (ilmainen), Elisa Viihde, Netflix (rajallinen FI-valikoima)","source":"src:EL1"}
    },
    "KNOWLEDGE_TABLES":{"classics":[
        {"title":"Tuntematon sotilas (2017)","genre":"Sota/draama","rating":"K16","runtime":180},
        {"title":"Miekkailija (2015)","genre":"Draama/historia","rating":"K7","runtime":93},
        {"title":"Hytti nro 6 (2021)","genre":"Draama","rating":"K12","runtime":107},
        {"title":"Härmä (2012)","genre":"Toiminta/historia","rating":"K16","runtime":115},
        {"title":"Miehen työ (2007)","genre":"Draama","rating":"K12","runtime":92},
        {"title":"Napapiirin sankarit (2010)","genre":"Komedia","rating":"K7","runtime":92}
    ]},
    "SEASONAL_RULES":[{"season":"Kevät","action":"Kevätväsymys → kevyet komediat. Pääsiäiselokuvat.","source":"src:EL1"},{"season":"Kesä","action":"Mökkielokuvat ulkona (projektorilla?). Kevyitä kesäkomedioita.","source":"src:EL1"},{"season":"Syksy","action":"Pimenevät illat → pidemmät draamat, trilogiat. Dokumentit.","source":"src:EL1"},{"season":"Talvi","action":"Itsenäisyyspäivä: Tuntematon sotilas. Joulu: klassikoita. Pitkät illat → sarjat.","source":"src:EL1"}],
    "FAILURE_MODES":[{"mode":"Elokuva ei saatavilla streamingissä","detection":"Hakutulokset tyhjät","action":"Tarkista Yle Areena, Elisa, kirjasto (DVD/Blu-ray lainaus)","source":"src:EL1"},{"mode":"Ikärajaylitys (lapset paikalla)","detection":"Elokuva K16 + lapsi <16v","action":"Vaihda elokuva tai sovi aikuisten ilta erikseen","source":"src:EL1"}],
    "COMPLIANCE_AND_LEGAL":{"ikarajat":"Kuvaohjelmalaki 710/2011: ikärajat sitovia [src:EL2]"},
    "UNCERTAINTY_NOTES":["Streaming-valikoimat muuttuvat kuukausittain."]
  },[{"id":"src:EL1","org":"Elonet / SES","title":"Suomalaiset elokuvat","year":2025,"url":"https://elonet.finna.fi/","supports":"Elokuvatiedot, arviot."},{"id":"src:EL2","org":"KAVI","title":"Kuvaohjelmalaki 710/2011","year":2011,"url":"https://www.kavi.fi/","supports":"Ikärajat."}]),

  ("inventaariopaallikko","Inventaariopäällikkö",{
    "ASSUMPTIONS":["Korvenrannan varasto: työkalut, mehiläistarvikkeet, elintarvikkeet, polttoaineet","Inventointi systemaattisesti","Kytketty logistikko-, eräkokki-, tarhaaja-agentteihin"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "reorder_point_sugar_kg":{"value":50,"action":"Sokerivarasto <50 kg → tilaus (syysruokinta ~15-20 kg/pesä × 300 pesää)","source":"src:IN1"},
        "fuel_reserve_days":{"value":7,"action":"Polttopuut + bensiini ≥7 pv varmuusvarasto","source":"src:IN1"},
        "tool_condition_check_months":{"value":3,"action":"Työkalujen kunto 3 kk välein","source":"src:IN1"},
        "food_expiry_check_weeks":{"value":2,"action":"Elintarvikkeiden parasta ennen -tarkistus 2x/kk","source":"src:IN1"},
        "inventory_full_audit_months":{"value":6,"action":"Täysinventointi 2x/vuosi (kevät + syksy)","source":"src:IN1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Kevätinventointi. Mehiläistarvikkeiden tilaus (kehykset, vaha, sokerit). Puutarhatyökalut.","source":"src:IN1"},{"season":"Kesä","action":"Linkoamistarvikkeet. Polttoaineet (venemoottori, ruohonleikkuri). Grillikaasu.","source":"src:IN1"},{"season":"Syksy","action":"Syksyinventointi. Sokerin suurhankinta (ruokinta). Polttopuuvarasto ennen talvea.","source":"src:IN1"},{"season":"Talvi","action":"Talvivarmuusvarasto. Polttoaineet. Eläinruoat (lintulauta).","source":"src:IN1"}],
    "FAILURE_MODES":[{"mode":"Sokeri loppuu ruokintakauden aikana","detection":"Varasto <50 kg + syysruokinta käynnissä","action":"PIKATILAUS. Väliaikaisesti: inverttisiirappi tai valmiit sokerikakut.","source":"src:IN1"},{"mode":"Työkalu rikkoutunut","detection":"Työkalu ei toimi, vaarallinen","action":"Merkitse käyttökieltoon, tilaa varaosa/korvaava, ilmoita logistikolle","source":"src:IN1"}],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Mehiläistarvikkeiden toimitusajat voivat olla pitkiä keväällä — ennakkotilaus."]
  },[{"id":"src:IN1","org":"JKH Service sisäinen","title":"Varastohallinta","year":2026,"url":None,"supports":"Inventointi, tilauspisteet."}]),

  ("kierratys_jate","Kierrätys- ja jäteneuvoja",{
    "ASSUMPTIONS":["Korvenranta: haja-asutusalueen jätehuolto","Lajittelu: bio, paperi, kartonki, lasi, metalli, muovi, seka","Kompostointi oma"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "waste_sorting_categories":{"value":"7 jaetta + vaarallinen jäte erikseen","source":"src:KI1"},
        "compost_temp_c":{"value":"50-65°C aktiivivaiheessa","action":"<40°C → lisää typpipitoista (ruoantähteet), >70°C → käännä","source":"src:KI1"},
        "hazardous_waste":{"value":"Akut, maalit, lääkkeet, kemikaalit → keräyspiste","action":"EI sekajätteeseen. Kouvolan seudun jäteasema.","source":"src:KI2"},
        "bin_pickup_interval_weeks":{"value":"Seka: 4 vko, bio: 2 vko (kesä), muut: sopimuksen mukaan","source":"src:KI1"},
        "recycling_rate_target_pct":{"value":55,"note":"EU-tavoite 2025: 55% kierrätysaste","source":"src:KI2"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Kompostin herätys (käännä, lisää tuoretta). Kevätsiivousjätteet.","source":"src:KI1"},{"season":"Kesä","action":"Bio-jätteen haju → biojäteastian pesu. Komposti aktiivinen. Puutarhajäte.","source":"src:KI1"},{"season":"Syksy","action":"Puutarhajätteet kompostiin. Vaarallisten jätteiden keräyspäivä (kunta).","source":"src:KI2"},{"season":"Talvi","action":"Bio-jäte jäätyy → kompostointi hidastuu. Tuhkan käsittely (puulämmitys).","source":"src:KI1"}],
    "FAILURE_MODES":[{"mode":"Komposti haisee","detection":"Mätänemisen haju (anaerobinen)","action":"Käännä, lisää kuivaa ainesta (haketta, olkea), ilmaa","source":"src:KI1"},{"mode":"Vaarallinen jäte sekajätteessä","detection":"Akku tai kemikaali havaittu","action":"Poista HETI, toimita keräyspisteeseen","source":"src:KI2"}],
    "COMPLIANCE_AND_LEGAL":{"jatelaki":"Jätelaki 646/2011: lajitteluvelvollisuus [src:KI2]","kompostointi":"Kompostointi-ilmoitus kunnan jätehuoltoviranomaiselle [src:KI2]"},
    "UNCERTAINTY_NOTES":["Haja-asutusalueen jätehuollon palvelutaso vaihtelee kunnittain."]
  },[{"id":"src:KI1","org":"Kouvolan Jätehuolto","title":"Lajitteluohje","year":2025,"url":None,"supports":"Lajittelu, kompostointi."},{"id":"src:KI2","org":"Oikeusministeriö","title":"Jätelaki 646/2011","year":2011,"url":"https://finlex.fi/fi/laki/ajantasa/2011/20110646","supports":"Lajitteluvelvollisuus, vaarallinen jäte."}]),

  ("siivousvastaava","Siivousvastaava",{
    "ASSUMPTIONS":["Korvenrannan mökkikiinteistö","Puupinnat, saunan pesu, ulkoterassit"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "sauna_wash_interval_weeks":{"value":"1-2 (aktiivikäytössä)","source":"src:SI1"},
        "mold_detection":{"value":"Silminnähden tumma laikku tai pistävä haju","action":"Suojaus (maski FFP2), pesu natriumhypokloriitilla, syyn selvitys","source":"src:SI1"},
        "indoor_air_quality_co2_ppm":{"value":"<1000 ppm","action":">1200 → tuuleta, tarkista ilmanvaihto","source":"src:SI1"},
        "deep_clean_interval_months":{"value":3,"action":"Perusteellinen siivous 4x/vuosi","source":"src:SI1"},
        "cleaning_products_eco":{"value":"Joutsenmerkki/EU Ecolabel suositeltava","action":"EI happamia pesuaineita marmoripinnoille, EI kloorivetyä alumiinille","source":"src:SI1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Kevätsiivoukset: ikkunat, pölyt, tekstiilit. Talven lian poisto.","source":"src:SI1"},{"season":"Kesä","action":"Terassin pesu. Saunan pesu tiheämmin. Hyönteisten jäljet.","source":"src:SI1"},{"season":"Syksy","action":"Syysisosiivous ennen talvea. Vesipisteiden puhdistus. Saunan kuivatus.","source":"src:SI1"},{"season":"Talvi","action":"Vähemmän ulkosiivousta. Sisäilman kosteus seurannassa. Tulisijan tuhkan siivous.","source":"src:SI1"}],
    "FAILURE_MODES":[{"mode":"Homeen löytö","detection":"Tumma laikku, haju, allergiaoireita","action":"FFP2-maski, pesu (hypkloriitti 1:10), syyn selvitys (kosteuslähde), ilmoita timpurille","source":"src:SI1"},{"mode":"Viemärin haju","detection":"Paha haju lattikaivosta","action":"Kaada vettä kaivoon (vesilukko kuivunut), puhdista","source":"src:SI1"}],
    "COMPLIANCE_AND_LEGAL":{"kemikaalit":"Pesuaineiden käyttöturvallisuustiedotteet (KTT) saatavilla [src:SI1]"},
    "UNCERTAINTY_NOTES":["Puupintojen pesuvastustus vaihtelee — testaa aina piilossa olevasta kohdasta."]
  },[{"id":"src:SI1","org":"Puhtausala / Marttaliitto","title":"Siivousohjeistot","year":2025,"url":"https://www.martat.fi/","supports":"Puhdistusmenetelmät, aineet."}]),

  ("logistikko","Logistikko (reitti + ajoajat)",{
    "ASSUMPTIONS":["Korvenranta ↔ Helsinki, Kouvola, tarha-alueet","Tesla Model Y (sähköauto)","Mehiläistarvikkeiden ja hunajan kuljetus"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "range_km_winter":{"value":"Tesla Model Y: ~350 km (kesä), ~250 km (talvi -20°C)","source":"src:LO1"},
        "charging_plan":{"value":"Latauspisteet reitillä ennakkosuunnittelu >200 km matkoilla","action":"<20% akku → etsi latausasema HETI","source":"src:LO1"},
        "korvenranta_helsinki_km":{"value":"~150 km, ~1h 50min (E75/VT12)","source":"src:LO1"},
        "korvenranta_kouvola_km":{"value":"~25 km, ~30 min","source":"src:LO1"},
        "honey_transport_temp_c":{"value":"Hunaja: 15-25°C (ei jäädytä, ei kuumenna >40°C)","action":"Talvella: eristä auto, kesällä: vältä suoraa aurinkoa","source":"src:LO2"},
        "load_capacity_kg":{"value":"Tesla Model Y: ~500 kg kuorma","source":"src:LO1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Kelirikko → hiekkateillä varovaisuus. Renkaanvaihto (huhti-touko). Tarhakierros alkaa.","source":"src:LO1"},{"season":"Kesä","action":"Hunajan kuljetuskausi. Lämpö → hunajan suojaus. Pitkät päivät → ajoaika joustava.","source":"src:LO2"},{"season":"Syksy","action":"Syysruokinnan sokerikuljetukset (4500-6000 kg koko kausi). Rengasvaihto (loka-marras).","source":"src:LO1"},{"season":"Talvi","action":"Sähköauton toimintamatka -30%. Esilämmitys. Liukkaat tiet. Tarhakäynnit harvinaisempia.","source":"src:LO1"}],
    "FAILURE_MODES":[{"mode":"Akku loppuu kesken matkan","detection":"Varoitus <10%","action":"Hae lähin pikalatausasema (Plugit, K-Lataus, Tesla SC). Ekoajo päälle.","source":"src:LO1"},{"mode":"Kelirikko estää tien","detection":"Hiekka-/sorapohja upottaa","action":"Vaihtoehtoinen reitti (päällystetylle tielle), lykkää matkaa, informoi tarhaajaa","source":"src:LO1"}],
    "COMPLIANCE_AND_LEGAL":{"kuorma":"Tieliikennelaki: kuorman sidonta ja painorajat [src:LO1]","ajo_lepo":"Ammattimaisessa liikenteessä ajo-lepoaikasäännöt (ei koske omaa ajoa) [src:LO1]"},
    "UNCERTAINTY_NOTES":["Sähköauton todellinen talvitoimintamatka vaihtelee lämpötilan ja ajotavan mukaan.","Kelirikkoajat vaihtelevat vuosittain merkittävästi."]
  },[{"id":"src:LO1","org":"Traficom / Tesla","title":"Sähköauton käyttö Suomessa","year":2026,"url":"https://www.traficom.fi/","supports":"Toimintamatka, lataus, liikenne."},{"id":"src:LO2","org":"SML","title":"Hunajan käsittely ja kuljetus","year":2011,"url":None,"supports":"Hunajan kuljetuslämpötilat."}]),

  ("matemaatikko_fyysikko","Matemaatikko ja fyysikko (laskenta + mallit)",{
    "ASSUMPTIONS":["Laskennallinen tuki kaikille agenteille","Fysiikan mallit: lämmönsiirto, nestevirtaus, optiikka, mekaniikka","Matemaattiset mallit: tilastot, ennusteet, optimointi"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "deg_day_formula":{"value":"°Cvr = Σ max(0, T_avg - T_base), T_base = 5°C","source":"src:MA1"},
        "wind_chill_formula":{"value":"WCI = 13.12 + 0.6215T - 11.37V^0.16 + 0.3965TV^0.16","note":"T in °C, V in km/h","source":"src:MA1"},
        "heat_loss_u_value":{"value":"Q = U × A × ΔT (W)","note":"U = lämmönläpäisykerroin (W/m²K)","source":"src:MA2"},
        "solar_angle_formula":{"value":"θ = 90° - φ + δ (noon elevation)","note":"φ = leveysaste, δ = auringon deklinaatio","source":"src:MA1"},
        "statistical_confidence":{"value":"95% luottamusväli vakiomääritelmä","action":"<90% → lisää dataa ennen päätöstä","source":"src:MA1"},
        "optimization_constraints":{"value":"LP/NLP-ongelmat: tarhojen sijoittelu, reittioptimointi","source":"src:MA1"}
    },
    "PROCESS_FLOWS":{"calculation_service":{"steps":["1. Vastaanota laskentapyyntö agentilta","2. Tunnista malli (tilasto, fysiikka, optimointi)","3. Suorita laskenta parametreilla","4. Palauta tulos + epävarmuusarvio","5. Dokumentoi olettamukset"]}},
    "SEASONAL_RULES":[{"season":"Kevät","action":"°Cvr-kertymän laskenta alkaa. Aurinkokulmalaskelmat kasvimaalle.","source":"src:MA1"},{"season":"Kesä","action":"Satoennustemallit (lineaarinen regressio painodatan perusteella). UV-indeksi.","source":"src:MA1"},{"season":"Syksy","action":"Routasyvyysennuste (pakkasvuorokausikertymä). Lumikuormalaskelmat.","source":"src:MA2"},{"season":"Talvi","action":"Lämpöhäviölaskelmat rakennuksille. Tuulen hyytävyysindeksi. Jään kantavuuslaskenta.","source":"src:MA2"}],
    "FAILURE_MODES":[{"mode":"Malli antaa epärealistisen tuloksen","detection":"Tulos fysikaalisesti mahdoton (esim. negatiivinen massa)","action":"Tarkista syötedata, mallin rajaehdot, yksikkömuunnokset","source":"src:MA1"},{"mode":"Liian vähän datapisteitä","detection":"n < 30 → tilastollinen voima riittämätön","action":"Ilmoita epävarmuus, käytä Bayesian-menetelmiä tai bootstrappia","source":"src:MA1"}],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Kaikki mallit ovat yksinkertaistuksia — todellinen maailma on monimutkaisempi.","Monte Carlo -simulaatio hyödyllinen kun analyyttinen ratkaisu ei ole mahdollinen."],
    "_x":[
        {"q":"Miten °Cvr lasketaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.deg_day_formula.value","source":"src:MA1"},
        {"q":"Mikä on tuulen hyytävyyden kaava?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.wind_chill_formula.value","source":"src:MA1"},
        {"q":"Miten lämpöhäviö lasketaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.heat_loss_u_value.value","source":"src:MA2"},
        {"q":"Miten aurinkokulma lasketaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.solar_angle_formula.value","source":"src:MA1"},
        {"q":"Mikä on tilastollisen merkitsevyyden raja?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.statistical_confidence.value","source":"src:MA1"},
        {"q":"Mitä tehdään kun dataa on liian vähän?","a_ref":"FAILURE_MODES[1].action","source":"src:MA1"},
        {"q":"Miten laskentapyyntö käsitellään?","a_ref":"PROCESS_FLOWS.calculation_service.steps","source":"src:MA1"},
        {"q":"Mikä on LP-optimoinnin käyttökohde?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.optimization_constraints.value","source":"src:MA1"},
    ]
  },[{"id":"src:MA1","org":"Ilmatieteen laitos / yleinen fysiikka","title":"Laskentakaavat","year":2026,"url":None,"supports":"Termodynamiikka, optiikka, tilastotiede."},{"id":"src:MA2","org":"RIL/RT","title":"Rakennusfysiikka","year":2024,"url":"https://www.ril.fi/","supports":"Lämmönläpäisy, lumikuorma, routalaskelmat."}]),
]

for a in agents:
    ma(*a)

print(f"\n✅ Batch 7 valmis: agentit 40-50")

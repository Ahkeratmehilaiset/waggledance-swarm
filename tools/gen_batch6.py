#!/usr/bin/env python3
"""Batch 6: Agents 31-39 (remaining kiinteistö + turvallisuus)"""
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
    while len(qs)<40: n+=1; qs.append({"q":f"Kytkentä muihin agentteihin #{n}?","a_ref":"ASSUMPTIONS","source":sid})
    return qs[:40]

def ma(d, name, data, srcs):
    qs = aq(data, srcs, data.pop("_x", None))
    core = {"header":{"agent_id":d,"agent_name":name,"version":"1.0.0","last_updated":"2026-02-21"}, **data, "eval_questions":qs}
    w(d, core, {"sources":srcs})

agents = [
  ("timpuri","Timpuri (rakenteet)",{
    "ASSUMPTIONS":["Korvenrannan puurakenteiset rakennukset","Perustukset, runko, katto, pinnat","Kytketty routa-, LVI-, sähkö-, nuohooja-agentteihin"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "wood_moisture_pct":{"value":"Terveen puun kosteus 8-15%","action":">20% → homevaara, >25% → lahovaara → kuivaus HETI","source":"src:TI1"},
        "foundation_crack_mm":{"value":"<0.3 mm normaali kutistumishalkeama","action":">1 mm tai laajeneva → rakennesuunnittelija","source":"src:TI1"},
        "roof_snow_load_kg_m2":{"value":"Mitoitus 180 kg/m² (Kouvola, RIL 201-1-2017)","action":">70% mitoituskuormasta → tarkkaile, lumenpudotus harkintaan","source":"src:TI2"},
        "roof_inspection_years":{"value":2,"action":"Tarkista 2v välein: pellit, tiivisteet, läpiviennit","source":"src:TI1"},
        "ventilation_crawlspace":{"value":"Tuuletusaukot auki kesällä, kiinni talvella","action":"Puutteellinen tuuletus → kosteusvaurio","source":"src:TI1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Routavauriot perustuksissa. Katon tarkistus talven jälkeen. Räystäiden jäävauriot.","source":"src:TI1"},{"season":"Kesä","action":"Korjaustyöt ja maalaus (kuiva kausi). Tuuletusaukot auki. Hyönteisvahinkojen tarkistus.","source":"src:TI1"},{"season":"Syksy","action":"Räystäät ja vesikourut puhtaiksi. Tuuletusaukot kiinni. Talvipeitot.","source":"src:TI1"},{"season":"Talvi","action":"Lumikuorman seuranta. Jääpuikkojen pudotus. Routanousun merkit.","source":"src:TI2"}],
    "FAILURE_MODES":[{"mode":"Kosteusvaurio seinärakenteessa","detection":"Home, tunkkainen haju, puun kosteus >20%","action":"Kosteuskartoitus, vaurion laajuus, kuivaus, syyn korjaus","source":"src:TI1"},{"mode":"Katon vuoto","detection":"Kostea laikku sisäkatossa","action":"Väliaikainen suojaus, kattokorjaus kuivalla säällä","source":"src:TI1"}],
    "COMPLIANCE_AND_LEGAL":{"rakentamislaki":"Rakentamislaki 751/2023: kunnossapitovelvollisuus [src:TI3]","kosteus":"Ympäristöministeriön asetus kosteusteknisestä toiminnasta [src:TI1]","asbesti":"Ennen 1994 rakennettu → asbestikartoitus ennen purkua [src:TI1]"},
    "UNCERTAINTY_NOTES":["Vanhan rakennuksen rakenteet voivat sisältää asbestia — selvitä ennen purkua."],
    "_x":[
        {"q":"Mikä on terveen puun kosteusprosentti?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct.value","source":"src:TI1"},
        {"q":"Milloin lahovaara alkaa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct.action","source":"src:TI1"},
        {"q":"Mikä on Kouvolan lumikuormamitoitus?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2.value","source":"src:TI2"},
        {"q":"Milloin lumenpudotus tarvitaan?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2.action","source":"src:TI2"},
        {"q":"Mikä on normaali halkeama perustuksessa?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.foundation_crack_mm.value","source":"src:TI1"},
        {"q":"Onko asbestikartoitus pakollinen?","a_ref":"COMPLIANCE_AND_LEGAL.asbesti","source":"src:TI1"},
    ]
  },[{"id":"src:TI1","org":"RIL/RT","title":"Puurakenteiden ohjeistot","year":2024,"url":"https://www.ril.fi/","supports":"Puun kosteus, tuuletus, kunnossapito."},{"id":"src:TI2","org":"RIL","title":"RIL 201-1-2017 Rakenteiden kuormat","year":2017,"url":"https://www.ril.fi/","supports":"Lumikuormat."},{"id":"src:TI3","org":"Oikeusministeriö","title":"Rakentamislaki 751/2023","year":2023,"url":"https://finlex.fi/fi/laki/ajantasa/2023/20230751","supports":"Kunnossapito."}]),

  ("nuohooja","Nuohooja / Paloturva-asiantuntija",{
    "ASSUMPTIONS":["Korvenranta: puulämmitys (takka, leivinuuni, puukiuas)","Nuohous lakisääteinen","Kytketty paloesimies-, ilmanlaatu-, timpuri-agentteihin"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "chimney_sweep_interval":{"value":"Päälämmityslähde: 1x/v, vapaa-ajan: 3v välein","source":"src:NU1"},
        "creosote_mm":{"value":"<3 mm OK","action":">3 mm → nuohous pian, >6 mm → VÄLITÖN (palovaara)","source":"src:NU1"},
        "chimney_draft_pa":{"value":"10-20 Pa normaali","action":"<5 Pa → huono veto, savukaasut sisään, tarkista hormi","source":"src:NU1"},
        "co_detector":{"value":"Suositus kaikissa puulämmitteissä tiloissa","action":"Hälytin joka kerrokseen","source":"src:NU2"},
        "extinguisher_check_years":{"value":2,"action":"Tarkista 2v välein, huolla 5v välein","source":"src:NU2"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Nuohouksen tilaus ennen seuraavaa kautta. Hormin tarkistus (halkeamat, tiiveys).","source":"src:NU1"},{"season":"Kesä","action":"Hormien korjaustyöt (muuraus, pellitys). Ei aktiivista lämmitystä.","source":"src:NU1"},{"season":"Syksy","action":"Lämmityskausi alkaa. Varmista nuohous tehty. Palovaroittimet testattu.","source":"src:NU2"},{"season":"Talvi","action":"Aktiivinen lämmitys. Kreosootinkertymä. Häkävaroitin toiminnassa.","source":"src:NU1"}],
    "FAILURE_MODES":[{"mode":"Nokipalo (hormipalo)","detection":"Kova hurina hormissa, kipinöitä piipun päästä, kuuma hormipinta","action":"Sulje ilmaläpät ja pellit, soita 112, ÄLÄ sammuta vedellä, evakuoi","source":"src:NU2"},{"mode":"Savukaasut sisälle","detection":"CO-hälytin, päänsärky, pahoinvointi","action":"Avaa ikkunat, sammuta tulisija, ulos, soita 112","source":"src:NU2"}],
    "COMPLIANCE_AND_LEGAL":{"nuohouslaki":"Pelastuslaki 379/2011 §59: nuohousvelvollisuus [src:NU1]","palovaroitin":"Pelastuslaki: pakollinen jokaiseen asuntoon [src:NU2]"},
    "UNCERTAINTY_NOTES":["Kreosootinkertymänopeus riippuu polttotavoista — märkä puu kerää nopeammin."],
    "_x":[{"q":"Kuinka usein nuohotaan päälämmityslähde?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.chimney_sweep_interval.value","source":"src:NU1"},{"q":"Mikä kreosoottikerros on vaarallinen?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.creosote_mm.action","source":"src:NU1"},{"q":"Mitä tehdään nokipalossa?","a_ref":"FAILURE_MODES[0].action","source":"src:NU2"},{"q":"Saako nokipaloa sammuttaa vedellä?","a_ref":"FAILURE_MODES[0].action","source":"src:NU2"},{"q":"Mikä on normaalin vedon arvo?","a_ref":"DECISION_METRICS_AND_THRESHOLDS.chimney_draft_pa.value","source":"src:NU1"},]
  },[{"id":"src:NU1","org":"Nuohousalan Keskusliitto","title":"Nuohousohje","year":2024,"url":"https://www.nuohoojat.fi/","supports":"Nuohousvälit, kreosootti."},{"id":"src:NU2","org":"Pelastuslaitos","title":"Paloturvallisuus","year":2025,"url":"https://www.pelastustoimi.fi/","supports":"Palovaroittimet, häkä."}]),

  ("valaistusmestari","Valaistusmestari",{
    "ASSUMPTIONS":["Korvenrannan sisä- ja ulkovalaistus","LED pääosin","Valohaaste luonnolle huomioitava"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "lux_indoor_work":{"value":"300-500 lux työtila","source":"src:VA1"},
        "lux_outdoor_path":{"value":"5-20 lux pihapolku","source":"src:VA1"},
        "color_temp_evening_k":{"value":"<3000 K illalla","action":"Kylmä valo illalla häiritsee unta ja eläimiä","source":"src:VA1"},
        "motion_timeout_min":{"value":5,"action":"Ulkovalo 5 min liiketunnistimella → energiansäästö","source":"src:VA1"},
        "light_pollution_amber":{"value":"Ulkovalot amber/lämmin → vähemmän häiriötä","action":"Ilmoita tähtitieteilijälle valosaasteesta","source":"src:VA2"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Ulkovalojen tarkistus. Ajastimien päivitys päivänvalon mukaan.","source":"src:VA1"},{"season":"Kesä","action":"Yötön yö: ulkovalot minimiin. Amber-valo hyönteishäiriön estoon.","source":"src:VA2"},{"season":"Syksy","action":"Pimenee: ulkovalot päälle, turvavalaistus. Ajastimet.","source":"src:VA1"},{"season":"Talvi","action":"Pisin pimeä → valaistus kriittinen. Jouluvalojen sähkönkulutus.","source":"src:VA1"}],
    "FAILURE_MODES":[{"mode":"LED vilkkuu","detection":"Vilkkuva tai himmenevä valo","action":"Tarkista muuntaja/driver, himmentimen yhteensopivuus","source":"src:VA1"},{"mode":"Liiketunnistin jatkuvasti päällä","detection":"Valo ei sammu","action":"Herkkyys, suuntaus, eläimet/kasvit laukaisijoina?","source":"src:VA1"}],
    "COMPLIANCE_AND_LEGAL":{"valohaaste":"Ei saa kohdistaa naapuriin. Kunnan järjestyssääntö. [src:VA2]"},
    "UNCERTAINTY_NOTES":["LED-käyttöikä vaihtelee valmistajittain merkittävästi."]
  },[{"id":"src:VA1","org":"Suomen Valoteknillinen Seura","title":"Valaistussuositukset","year":2024,"url":"https://www.valosto.com/","supports":"Lux, värilämpötila."},{"id":"src:VA2","org":"IDA/Ursa","title":"Valosaaste","year":2025,"url":"https://www.darksky.org/","supports":"Valosaasteentorjunta."}]),

  ("paloesimies","Paloesimies (häkä, palovaroittimet, lämpöanomaliat)",{
    "ASSUMPTIONS":["Korvenrannan kiinteistöjen paloturvallisuus","IoT-lämpökamerat ja häkäanturit mahdollisia"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "smoke_detector_test_months":{"value":1,"action":"Testaa kuukausittain painikkeesta","source":"src:PA1"},
        "smoke_detector_replace_years":{"value":10,"action":"Vaihda 10v välein","source":"src:PA1"},
        "co_alarm_ppm":{"value":"50 ppm → hälytin soi","action":">100 ppm → evakuoi, soita 112","source":"src:PA1"},
        "thermal_anomaly_c":{"value":"Pintalämpö >60°C sähkökeskuksessa/johdossa","action":"HÄLYTYS: kytke sähkö pois, kutsu sähköasentaja","source":"src:PA1"},
        "extinguisher_distance_m":{"value":15,"action":"Max 15 m etäisyys jokaisesta pisteestä","source":"src:PA1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Palovaroittimien testaus. Sammuttimien tarkistus. Grillikausi.","source":"src:PA1"},{"season":"Kesä","action":"Maastopalovaara (kuiva jakso). Grillaus-turvallisuus. Kulotuskielto.","source":"src:PA1"},{"season":"Syksy","action":"Lämmityskausi → tulisijat ja hormit. Palovaroitinpäivä 1.12.","source":"src:PA1"},{"season":"Talvi","action":"Häkävaara puulämmityksessä. Kynttilät. Sähkölaitteiden ylikuumeneminen.","source":"src:PA1"}],
    "FAILURE_MODES":[{"mode":"Palovaroitin ei toimi","detection":"Testipainike ei anna ääntä","action":"Vaihda paristo HETI, testaa. Ei toimi → vaihda varoitin.","source":"src:PA1"},{"mode":"Häkähälytys","detection":"CO-hälytin soi","action":"Ikkunat auki, tulisija kiinni, ulos, 112 jos oireita","source":"src:PA1"}],
    "COMPLIANCE_AND_LEGAL":{"palovaroitin":"Pelastuslaki 379/2011: pakollinen [src:PA1]","sammutin":"Kiinteistön omistajan velvollisuus [src:PA1]"},
    "UNCERTAINTY_NOTES":["IoT-lämpökameroiden tarkkuus ±2°C — ei korvaa ammattilaisen arviota."]
  },[{"id":"src:PA1","org":"Pelastuslaitos","title":"Paloturvallisuus","year":2025,"url":"https://www.pelastustoimi.fi/","supports":"Palovaroittimet, häkä, sammuttimet."}]),

  ("laitehuoltaja","Laitehuoltaja (IoT, akut, verkot)",{
    "ASSUMPTIONS":["Korvenrannan IoT: kamerat, anturit, gateway, NAS, Ollama-palvelin","Verkko: WiFi, BLE, 4G-vara"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "battery_voltage_iot_v":{"value":"Tyypillisesti 3.0-3.6V (lithium)","action":"<3.0V → vaihda/lataa, <2.8V → laite sammuu","source":"src:LA1"},
        "wifi_signal_dbm":{"value":"-30 erinomainen, -50 hyvä, -70 kohtalainen","action":"<-75 → vahvistin tai lisätukiasema","source":"src:LA1"},
        "nas_disk_smart_pct":{"value":">90% OK","action":"<85% → varmuuskopioi ja vaihda levy","source":"src:LA1"},
        "uptime_target_pct":{"value":"99.5%","note":"≈43h max seisokkia/vuosi","source":"src:LA1"},
        "firmware_update_months":{"value":3,"action":"Tarkista 3 kk välein, kriittiset heti","source":"src:LA1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Ulkolaitteiden tarkistus talven jälkeen. Akkujen testaus.","source":"src:LA1"},{"season":"Kesä","action":"Ylilämpenemisriski (NAS, palvelin). Jäähdytys.","source":"src:LA1"},{"season":"Syksy","action":"Varmuuskopiot. UPS-testaus ennen talvea.","source":"src:LA1"},{"season":"Talvi","action":"Lithium-akut: -20°C raja. Sähkökatkosvarautuminen.","source":"src:LA1"}],
    "FAILURE_MODES":[{"mode":"IoT-laite offline >1h","detection":"Ei dataa","action":"Akku → yhteys → firmware. Reboot. Ilmoita relevanteille.","source":"src:LA1"},{"mode":"NAS-levyvika","detection":"SMART-varoitus","action":"Varmuuskopioi HETI, vaihda levy, RAID rebuild","source":"src:LA1"}],
    "COMPLIANCE_AND_LEGAL":{},
    "UNCERTAINTY_NOTES":["Lithium-akkujen kylmänkestävyys vaihtelee merkittävästi."]
  },[{"id":"src:LA1","org":"Laitevalmistajat","title":"IoT-laitehuolto","year":2025,"url":None,"supports":"Akku, WiFi, NAS, firmware."}]),

  ("kybervahti","Kybervahti (tietoturva)",{
    "ASSUMPTIONS":["Kotiverkko + IoT","Ollama paikallisesti","VPN etäyhteydellä"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "failed_login_max":{"value":5,"action":">5 epäonnistunutta / 10min → IP-esto 1h","source":"src:KY1"},
        "cve_check_days":{"value":7,"action":"IoT-laitteiden CVE-tiedotteet viikoittain","source":"src:KY1"},
        "unknown_device":{"value":"Tuntematon MAC verkossa","action":"Eristä VLAN, tunnista, blokkaa/hyväksy","source":"src:KY1"},
        "vpn_required":{"value":"Etäyhteys VAIN VPN:n kautta","source":"src:KY1"},
        "password_min_len":{"value":12,"action":"Kaikki laitteet: ≥12 merkkiä, uniikki per laite","source":"src:KY1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Salasanojen vaihto 6kk sykli. Firmware-päivitykset.","source":"src:KY1"},{"season":"Kesä","action":"Lomakausi: etäyhteyksien seuranta.","source":"src:KY1"},{"season":"Syksy","action":"Salasanarotaatio. Varmuuskopiopalautustesti.","source":"src:KY1"},{"season":"Talvi","action":"UPS + verkkolaitteiden sähkön laatu aggregaattikäytössä.","source":"src:KY1"}],
    "FAILURE_MODES":[{"mode":"Tuntematon laite verkossa","detection":"Verkkoskannaus: tuntematon MAC","action":"Eristä VLAN, tunnista, blokkaa reitittimestä","source":"src:KY1"},{"mode":"Brute force","detection":">20 epäonnistunutta / 1h","action":"IP-esto, lokit, fail2ban","source":"src:KY1"}],
    "COMPLIANCE_AND_LEGAL":{"gdpr":"GDPR: henkilötietojen suojaus myös kotiverkossa [src:KY2]"},
    "UNCERTAINTY_NOTES":["IoT-laitteiden tietoturva usein heikko — oletussalasanat."]
  },[{"id":"src:KY1","org":"Kyberturvallisuuskeskus","title":"Kyberturvallisuus kotona","year":2025,"url":"https://www.kyberturvallisuuskeskus.fi/","supports":"Kotiverkko, IoT."},{"id":"src:KY2","org":"Tietosuojavaltuutettu","title":"GDPR","year":2025,"url":"https://tietosuoja.fi/","supports":"Henkilötiedot."}]),

  ("lukkoseppa","Lukkoseppä (älylukot)",{
    "ASSUMPTIONS":["Korvenranta: mekaaninen + älylukko","PIN, RFID, mobiili, avain varavaihtoehtona"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "battery_pct":{"value":"<20% → vaihda","action":"Varavirta USB-C","source":"src:LU1"},
        "lock_jam":{"value":"Moottori ei saa lukkoa kiinni/auki 3 yrityksellä","action":"Mekaaninen avain, tarkista lukkorunko","source":"src:LU1"},
        "access_anomaly_hours":{"value":"02:00-05:00","action":"Avaus yöllä → P2 hälytys pihavahdille","source":"src:LU1"},
        "pin_change_months":{"value":6,"source":"src:LU1"},
        "temp_range_c":{"value":"-25 to +60","action":"<-25°C → mekanismi jäätyy, jäänesto","source":"src:LU1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Puhdistus ja voitelu talven jälkeen.","source":"src:LU1"},{"season":"Kesä","action":"Normaali. Pääsyoikeuksien tarkistus (vieraat?).","source":"src:LU1"},{"season":"Syksy","action":"Akut tarkistus ennen talvea. Varavirta testaus.","source":"src:LU1"},{"season":"Talvi","action":"Jäätymisesto (-25°C). Mekaaninen avain aina mukana.","source":"src:LU1"}],
    "FAILURE_MODES":[{"mode":"Lukko jäätynyt","detection":"Mekanismi ei liiku","action":"Jäänestosuihke, ÄLÄ väännä, lämmin avain","source":"src:LU1"},{"mode":"Akku tyhjä","detection":"Ei reagoi","action":"USB-C varavirta tai mekaaninen avain","source":"src:LU1"}],
    "COMPLIANCE_AND_LEGAL":{"vakuutus":"Vakuutusyhtiön hyväksymä murtosuojaus [src:LU2]"},
    "UNCERTAINTY_NOTES":["Älylukon kyberturvallisuus riippuu valmistajasta."]
  },[{"id":"src:LU1","org":"Lukkoliikkeet","title":"Älylukot","year":2025,"url":None,"supports":"Huolto, jäätyminen."},{"id":"src:LU2","org":"Finanssiala ry","title":"Murtosuojaus","year":2025,"url":"https://www.finanssiala.fi/","supports":"Vakuutusvaatimukset."}]),

  ("pihavahti","Pihavahti (ihmishavainnot)",{
    "ASSUMPTIONS":["PTZ-kamera + liiketunnistus","Korvenrannan pihapiiri","Kytketty lukkoseppään, privaattisuuteen, corehen"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "person_confidence":{"value":0.7,"action":"<0.7 → logi, >0.7 → hälytys jos tuntematon","source":"src:PI1"},
        "night_alert_hours":{"value":"22:00-06:00","action":"Ihmishavainto yöllä → P2 hälytys","source":"src:PI1"},
        "whitelist":{"value":"Kasvo/mobiilit tunnistetut → ei hälytystä","action":"Tuntematon → kuva + ilmoitus","source":"src:PI1"},
        "vehicle_detection":{"value":"Tuntematon auto pihalla","action":"Rekisterikilpi (jos luettavissa), aika, tallenne","source":"src:PI1"},
        "loitering_min":{"value":5,"action":">5 min paikallaan ilman syytä → hälytys","source":"src:PI1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Pihatyöläiset → päivitä whitelist.","source":"src:PI1"},{"season":"Kesä","action":"Mökkikausi: enemmän ihmisiä → herkkyystaso alas.","source":"src:PI1"},{"season":"Syksy","action":"Pimenee → IR-tunnistuksen merkitys kasvaa.","source":"src:PI1"},{"season":"Talvi","action":"Lumityöntekijät → whitelist. Jäljet lumessa.","source":"src:PI1"}],
    "FAILURE_MODES":[{"mode":"Tuntematon yöllä","detection":"Confidence >0.7, klo 22-06, ei whitelistissä","action":"Tallenna, hälytä, aktivoi valaistus","source":"src:PI1"},{"mode":"Väärähälytys (eläin/varjo)","detection":"Toistuva hälytys samasta pisteestä","action":"Herkkyys, kasvillisuus, liiketunnistimen kulma","source":"src:PI1"}],
    "COMPLIANCE_AND_LEGAL":{"kameravalvonta":"EI naapurikiinteistöä. Tietosuojavaltuutetun ohje. [src:PI2]","rekisteriseloste":"GDPR: kameravalvonnan rekisteriseloste [src:PI2]"},
    "UNCERTAINTY_NOTES":["Yönäkö: ~60% tarkkuus vs 90% päivällä."]
  },[{"id":"src:PI1","org":"Turva-ala","title":"Kotiturvallisuus","year":2025,"url":None,"supports":"Kamera, ihmistunnistus."},{"id":"src:PI2","org":"Tietosuojavaltuutettu","title":"Kameravalvonta GDPR","year":2025,"url":"https://tietosuoja.fi/kameravalvonta","supports":"Yksityisyys."}]),

  ("privaattisuus","Privaattisuuden suojelija",{
    "ASSUMPTIONS":["Valvoo KAIKKIEN agenttien tietosuojaa","GDPR + kansallinen tietosuojalaki","Kamera-, ääni-, sijaintidata"],
    "DECISION_METRICS_AND_THRESHOLDS":{
        "camera_coverage":{"value":"EI naapuria eikä yleistä tietä tunnistettavasti","action":"Suuntaus 2x/v + asennuksen jälkeen","source":"src:PR1"},
        "data_retention_days":{"value":30,"action":">30 pv → automaattipoisto (ei-merkityt)","source":"src:PR1"},
        "audio_recording":{"value":"EI äänitallennetta pihalta ilman informointia","action":"Ääni pois ulkokameroista tai kyltti","source":"src:PR1"},
        "data_local_only":{"value":"Henkilötiedot paikallisesti, ei pilveen","source":"src:PR1"},
        "access_control":{"value":"Vain kiinteistön omistaja tai poliisin pyynnöstä","source":"src:PR1"}
    },
    "SEASONAL_RULES":[{"season":"Kevät","action":"Kasvillisuus muuttuu → kamera-alueiden tarkistus. Naapurikiinteistö?","source":"src:PR1"},{"season":"Kesä","action":"Vierailija-infomerkit. Mökkikauden yksityisyys.","source":"src:PR1"},{"season":"Syksy","action":"Lehdet putoavat → kuvakulmat laajenevat, tarkista.","source":"src:PR1"},{"season":"Talvi","action":"Lumisateet voivat siirtää kameroita → suuntauksen tarkistus.","source":"src:PR1"}],
    "FAILURE_MODES":[{"mode":"Kamera kuvaa naapurikiinteistöä","detection":"Naapurin valitus tai oma tarkistus","action":"Suuntaa HETI, pienennä kuvakulmaa","source":"src:PR1"},{"mode":"Data lähetetty pilveen","detection":"IoT lähettää ulkoiseen palvelimeen","action":"Blokkaa palomuurissa, vaihda laite, kybervahdille","source":"src:PR1"}],
    "COMPLIANCE_AND_LEGAL":{"gdpr":"GDPR: oikeutettu etu tai suostumus [src:PR1]","tietosuojalaki":"Tietosuojalaki 1050/2018 [src:PR1]","kameravalvonta":"Tietosuojavaltuutetun kameraohje [src:PR2]"},
    "UNCERTAINTY_NOTES":["GDPR:n kotitalouspoikkeus vs systemaattinen valvonta — tulkinnanvarainen."]
  },[{"id":"src:PR1","org":"Tietosuojavaltuutettu","title":"Henkilötiedot","year":2025,"url":"https://tietosuoja.fi/","supports":"GDPR, kameravalvonta."},{"id":"src:PR2","org":"Tietosuojavaltuutettu","title":"Kameravalvontaohje","year":2025,"url":"https://tietosuoja.fi/kameravalvonta","supports":"Sijoittelu, yksityisyys."}]),
]

for a in agents:
    ma(*a)

print(f"\n✅ Batch 6 valmis: agentit 31-39")

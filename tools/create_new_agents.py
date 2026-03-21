#!/usr/bin/env python3
"""Create 25 new agents for Phase 3: core.yaml + knowledge files."""
import os
import yaml

AGENTS = [
    # ── COTTAGE (3) ──
    {
        'id': 'well_water', 'name_fi': 'Kaivoveden laadunvalvonta',
        'profiles': ['cottage'], 'priority': 'medium',
        'skills': ['pH', 'rauta', 'mangaani', 'koliformit', 'näytteenotto', 'suodatus', 'UV-käsittely'],
        'assumptions': [
            'Mökkikiinteistön porakaivo tai rengaskaivo',
            'Veden käyttö juoma- ja pesuvetenä',
            'Näytteenotto 1-3 vuoden välein',
        ],
        'thresholds': {
            'ph_range': {'value': '6.5-9.5', 'action': 'Alle 6.5 tai yli 9.5 → testaa uudelleen', 'source': 'src:VL1'},
            'iron_mg_l': {'value': 0.2, 'action': '>0.2 mg/l → rautasuodatin', 'source': 'src:VL1'},
            'coliform_cfu_100ml': {'value': 0, 'action': '>0 → UV-desinfiointi tai klooraus', 'source': 'src:VL1'},
        },
        'knowledge': {
            'veden_laatu.yaml': [
                'Kaivovesi on Suomessa yleensä hapanta (pH 5.5-6.5) kallioporakaivoissa',
                'Rauta- ja mangaanipitoisuudet yleisiä Suomen kallioperässä',
                'E. coli -löydös vaatii välitöntä toimenpidettä',
                'Nitraatti >50 mg/l vaarallinen imeväisikäisille',
                'Fluoridi >1.5 mg/l vaikuttaa hampaisiin pitkäaikaisessa käytössä',
            ],
            'kaivon_huolto.yaml': [
                'Rengaskaivon kansi tulee olla tiivis — estää pintaveden ja eläinten pääsy',
                'Kaivon puhdistus ja desinfiointi 5-10 vuoden välein',
                'Porakaivon tuotto tarkistettava — heikkeneminen voi kertoa tukkeutumisesta',
                'Pumpun huolto: painekytkimen säätö, painesäiliön esipaine vuosittain',
            ],
            'suodattimet.yaml': [
                'UV-desinfiointi tuhoaa bakteerit ja virukset — ei vaadi kemikaaleja',
                'Aktiivihiilisuodatin poistaa hajua, makua, radonin ja orgaanisia yhdisteitä',
                'Rautasuodatin (mangaanidioksidi) hapettaa liukoisen raudan kiinteäksi',
                'Suodattimien huoltoväli tyypillisesti 6-12 kk',
            ],
        },
    },
    {
        'id': 'septic_manager', 'name_fi': 'Jätevesijärjestelmä',
        'profiles': ['cottage'], 'priority': 'medium',
        'skills': ['saostussäiliö', 'umpisäiliö', 'pienpuhdistamo', 'tyhjennysvälit', 'hajunpoisto'],
        'assumptions': [
            'Haja-asutusalueen kiinteistö',
            'Valtioneuvoston asetus 157/2017',
            'Kompostoiva kuivakäymälä vaihtoehto',
        ],
        'thresholds': {
            'emptying_interval_months': {'value': 12, 'action': 'Tyhjennettävä vähintään 1x/vuosi', 'source': 'src:JV1'},
            'sludge_level_pct': {'value': 33, 'action': '>33% lietettä → tyhjennys', 'source': 'src:JV1'},
        },
        'knowledge': {
            'jarjestelmat.yaml': [
                'Saostussäiliö + maasuodattamo: yleisin ratkaisu haja-asutusalueella',
                'Pienpuhdistamo: kompakti, vaatii sähköä ja huoltoa',
                'Umpisäiliö: kaikki jätevedet kerätään, sopii pienen kulutuksen kohteisiin',
                'Harmaavesisuodatin: pesuvesille kun WC on kuivakäymälä',
            ],
            'huolto.yaml': [
                'Saostussäiliön tyhjennys 1-2 kertaa vuodessa, loka-auto',
                'Pienpuhdistamon vuosihuolto: lietteenpoisto, ilmastin',
                'Maasuodattamon käyttöikä 20-30 vuotta oikein hoidettuna',
                'Talvisuojaus: routaeristys, ei tyhjentämistä pakkasella',
            ],
            'saadokset.yaml': [
                'Valtioneuvoston asetus 157/2017: haja-asutuksen jätevesien käsittely',
                'Rannan läheisyydessä (<100m) tiukemmat puhdistusvaatimukset',
                'Poikkeus: yli 68-vuotiaat vakituisesti asuvat',
                'Kunta voi myöntää lykkäystä enintään 5 vuotta',
            ],
        },
    },
    {
        'id': 'firewood', 'name_fi': 'Polttopuuhuolto',
        'profiles': ['cottage'], 'priority': 'low',
        'skills': ['puulajit', 'halkaisumitat', 'kuivumisaika', 'varastointi', 'kulutusarvio'],
        'assumptions': [
            'Tulisija- ja saunalämmitys mökillä',
            'Puut omasta metsästä tai ostettu',
            'Varastointi ulkona katoksessa',
        ],
        'thresholds': {
            'moisture_pct': {'value': 20, 'action': '<20% kosteus → valmis poltettavaksi', 'source': 'src:PP1'},
            'co_risk_moisture': {'value': 25, 'action': '>25% kosteus → häkäriski, nokeutuminen', 'source': 'src:PP1'},
        },
        'knowledge': {
            'puulajit.yaml': [
                'Koivu: 1300 kWh/m³ — suosituin polttopuu, kuivuu hyvin',
                'Mänty: 1100 kWh/m³ — hartsi aiheuttaa nokea, hyvä sytykkeeksi',
                'Kuusi: 1000 kWh/m³ — rätisee, nopea palaminen',
                'Leppä: 1000 kWh/m³ — vähän savua, sopii savustukseen',
                'Haapa: 900 kWh/m³ — polttaa nokea pois piipusta',
            ],
            'kuivaus.yaml': [
                'Halko kuivuu kesässä: pilkonta keväällä → valmis syksyllä',
                'Optimaali halkopituus: 25-33 cm takalle, 40 cm leivinuuniin',
                'Varastoi irti maasta lavoilla, katto päälle, sivut auki',
                'Tuore puu: ~50% kosteutta. Kuiva: <20%.',
            ],
            'lammitys.yaml': [
                'Varaava takka: 1 pesällinen koivuhalkoja ≈ 15-25 kWh',
                'Saunan kiuas: ~10 kg puuta yhtä saunomiskertaa kohden',
                'Mökin talvikäyttö: ~5-10 m³ halkoja kaudessa',
                'Nuohous pakollinen kerran vuodessa',
                'Häkävaroitin pakollinen tulisijahuoneistossa',
            ],
        },
    },
    # ── HOME (10) ──
    {
        'id': 'energy_advisor', 'name_fi': 'Sähkö- ja energiaoptimointi',
        'profiles': ['home', 'factory'], 'priority': 'high',
        'skills': ['spot-hinta', 'kiinteä sopimus', 'kulutusprofiili', 'aurinkopaneelit', 'lämpöpumppu'],
        'assumptions': ['Suomen sähkömarkkina (Nord Pool)', 'Kotitalous tai pienyritys'],
        'thresholds': {
            'spot_expensive_c_kwh': {'value': 20, 'action': '>20 c/kWh → siirrä kuormia halvemmalle tunnille', 'source': 'src:EN1'},
            'spot_cheap_c_kwh': {'value': 5, 'action': '<5 c/kWh → käynnistä lämminvesivaraaja', 'source': 'src:EN1'},
        },
        'knowledge': {
            'sahkosopimukset.yaml': [
                'Pörssisähkö: hinta vaihtelee tunneittain',
                'Kiinteä sopimus: vakaa hinta 12-24 kk',
                'Siirtomaksu: päivä/yö-tariffi, ei kilpailutettavissa',
                'Sähkövero 2.253 c/kWh (2026)',
            ],
            'spot_hinta.yaml': [
                'Nord Pool Spot: hinta julkaistaan klo 13 seuraavalle päivälle',
                'Halvimmat tunnit: yöllä (02-06) ja sunnuntaisin',
                'Kalleimmat tunnit: arkiaamuisin (07-09) ja alkuillasta (17-19)',
                'Negatiivinen hinta mahdollinen tuuli+aurinko ylituotannossa',
            ],
            'saasto_vinkit.yaml': [
                'LED-valaistus: 80% säästö vs hehkulamppu',
                'Huonelämpötilan lasku 1°C → ~5% säästö lämmityskustannuksissa',
                'Pyykinpesu yöllä spot-sähköllä: 3-10 c/pesu säästö',
                'Standby-kulutus: tyypillinen koti 50-100W jatkuvasti → 400-800 kWh/v',
            ],
            'lampopumput.yaml': [
                'Ilmalämpöpumppu: COP 3-4, investointi 1500-3000€, säästö 30-50%',
                'Maalämpö: COP 4-5, investointi 15000-25000€, säästö 60-70%',
                'ARA-avustus 2026: energiaremontteihin 15-40% tuki',
            ],
        },
    },
    {
        'id': 'smart_home', 'name_fi': 'Kodin automaatio ja IoT',
        'profiles': ['home'], 'priority': 'medium',
        'skills': ['Zigbee', 'Matter', 'Home_Assistant', 'Shelly', 'Tuya'],
        'assumptions': ['Langaton kotiautomaatio', 'Home Assistant tai vastaava'],
        'thresholds': {
            'zigbee_devices_max': {'value': 100, 'action': '>100 laitetta → toinen koordinaattori', 'source': 'src:SH1'},
        },
        'knowledge': {
            'protokollat.yaml': [
                'Zigbee: mesh-verkko, pieni virrankulutus, vaatii koordinaattorin',
                'Matter: uusi standardi, Apple/Google/Amazon tuki',
                'WiFi: suora yhteys, kuormittaa verkkoa',
                'Thread: IPv6-pohjainen mesh, Matterin kuljetuskerros',
            ],
            'laitteet.yaml': [
                'Shelly: WiFi-releet, dimmerit, REST-API',
                'Aqara: edullinen Zigbee-sensoriperhe',
                'Home Assistant Yellow: omistettu HA-laitteisto',
            ],
            'automaatiot.yaml': [
                'Automaatio: spot-hinta <5c → käynnistä lämminvesivaraaja',
                'Automaatio: kukaan ei kotona → laske lämpöä 2°C',
                'Automaatio: kosteus kylpyhuoneessa >70% → tuuletin päälle',
                'Automaatio: auringonlasku → sytytä ulkovalot',
            ],
        },
    },
    {
        'id': 'indoor_garden', 'name_fi': 'Huonekasvit ja sisäviljely',
        'profiles': ['home'], 'priority': 'low',
        'skills': ['huonekasvit', 'valojakso', 'kastelu', 'lannoitus', 'tuholaiset'],
        'assumptions': ['Kaupunkiasunnon sisäkasvit', 'Suomen pimeä talvi'],
        'thresholds': {
            'light_hours_min': {'value': 12, 'action': 'Trooppiset kasvit vaativat 12-16h — lisävalo talvella', 'source': 'src:IG1'},
        },
        'knowledge': {
            'kasvit.yaml': [
                'Peikonlehti (Monstera): helppo, kestää varjoa',
                'Kultalaakas (Epipremnum): ilmaa puhdistava',
                'Anopinkieli (Sansevieria): kuivuudenkestävä',
                'Yrttitarha: basilika, persilja, tilli — LED-valo + 14h',
            ],
            'valaistus.yaml': [
                'LED-kasvivalo: 30-50W riittää ikkunapenkille',
                'Täysspektri-LED: luonnollisempi, helppo käytettävyys',
                'Ajastin: automaattinen 14-16h valojakso talvella',
            ],
            'ongelmat.yaml': [
                'Kellastuvat lehdet: ylikastelu, ravinnepuute tai liian vähän valoa',
                'Tuholaiset: viherkärpäset (keltaliima-ansa), kilpitäi (saippualiuos)',
                'Home juurissa: liian tiivis multa + ylikastelu',
            ],
        },
    },
    {
        'id': 'apartment_board', 'name_fi': 'Taloyhtiöasiat',
        'profiles': ['home'], 'priority': 'medium',
        'skills': ['yhtiökokous', 'vastikkeet', 'hallitus', 'putkiremontti', 'isännöinti'],
        'assumptions': ['Suomalainen asunto-osakeyhtiö', 'Asunto-osakeyhtiölaki 2009'],
        'thresholds': {
            'maintenance_increase_pct': {'value': 15, 'action': '>15% vastikkeen korotus → selvitä syy', 'source': 'src:AB1'},
        },
        'knowledge': {
            'yhtiokokous.yaml': [
                'Varsinainen yhtiökokous 6 kk kuluessa tilikauden päättymisestä',
                'Äänioikeus osakkeiden mukaan',
                'Ylimääräinen yhtiökokous: 1/10 osakkeenomistajista voi vaatia',
                'Etäosallistuminen mahdollista jos yhtiöjärjestyksessä sallittu',
            ],
            'vastikkeet.yaml': [
                'Hoitovastike: juoksevat kulut (sähkö, vesi, siivous)',
                'Rahoitusvastike: lainanlyhennys (esim. putkiremontti)',
                'Kotitalousvähennys taloyhtiön remonttien työ-osuudesta',
            ],
            'remontit.yaml': [
                'Putkiremontti: 500-1200 €/m², kesto 2-4 kk/rappu, 50v jakso',
                'Julkisivuremontti: 200-600 €/m², 30-50v jakso',
                'Pitkän tähtäimen suunnitelma (PTS): 10v, pakollinen',
            ],
        },
    },
    {
        'id': 'delivery_tracker', 'name_fi': 'Paketit ja tilaukset',
        'profiles': ['home'], 'priority': 'low',
        'skills': ['Posti', 'Matkahuolto', 'DHL', 'UPS', 'pakettiautomaatti'],
        'assumptions': ['Verkko-ostaminen Suomessa', 'Tulli EU-ulkopuolisille'],
        'thresholds': {
            'customs_threshold_eur': {'value': 150, 'action': '>150€ EU:n ulkopuolelta → tulliselvitys + ALV', 'source': 'src:DT1'},
        },
        'knowledge': {
            'palvelut.yaml': [
                'Posti: pakettiautomaatit, SmartPost, Posti.fi-seuranta',
                'Matkahuolto: bussipaketti, noutopiste',
                'DHL Express: nopea kansainvälinen, tulliselvitys mukana',
                'Budbee/Instabox: samapäivätoimitus isoihin kaupunkeihin',
            ],
            'seuranta.yaml': [
                'OmaPosti-sovellus: kaikkien Postin lähetysten seuranta',
                'Paketti noudettava 7 vrk kuluessa automaatista',
                'Peruutusoikeus verkko-ostoksissa: 14 vrk (kuluttajansuojalaki)',
            ],
        },
    },
    {
        'id': 'commute_planner', 'name_fi': 'Liikkuminen ja työmatka',
        'profiles': ['home'], 'priority': 'medium',
        'skills': ['HSL', 'VR', 'liikenne', 'pyöräily', 'sähköpotkulauta'],
        'assumptions': ['Pääkaupunkiseutu (HSL-alue)', 'Valtakunnallinen VR-liikenne'],
        'thresholds': {
            'commute_time_min': {'value': 45, 'action': '>45 min → harkitse etätyötä', 'source': 'src:CP1'},
        },
        'knowledge': {
            'hsl.yaml': [
                'HSL-sovellus: reaaliaikainen reittisuunnittelu ja mobiililippu',
                'Kausiliput: AB-vyöhyke ~62€/kk (2026)',
                'Työsuhde-etu: HSL-lippu verovapaasti (max 3400€/v)',
            ],
            'reitit.yaml': [
                'P+R (Park and Ride): juna-asemien liityntäpaikat',
                'Sähköpotkulauta: Tier, Voi — ~0.25€/min',
                'Kimppakyyti: BlaBla Car Suomi',
            ],
            'sahkoliikenne.yaml': [
                'Sähköauton kotilataus: 3.6-11 kW',
                'Julkiset laturit: Virta, Recharge, K-Lataus',
                'Sähköpyörä: 250W max, 25 km/h avustusraja',
            ],
        },
    },
    {
        'id': 'noise_monitor', 'name_fi': 'Melu ja ääniympäristö',
        'profiles': ['home'], 'priority': 'low',
        'skills': ['dB-tasot', 'rakennustyöt', 'hiljaisuusajat', 'äänieristys'],
        'assumptions': ['Kerrostaloasuminen', 'Taloyhtiön järjestyssäännöt'],
        'thresholds': {
            'night_noise_db': {'value': 30, 'action': '>30 dB yöaikaan → häiritsevä', 'source': 'src:NM1'},
        },
        'knowledge': {
            'raja_arvot.yaml': [
                'Yöaikaan (22-07) asunnossa: ohjearvo 30 dB, päivällä 35 dB',
                'Hiljaisuusaika: tyypillisesti 22-07 ja sunnuntaisin',
                'Remonttityöt: arkisin 07-18, ei pyhäpäivinä',
                'Häiritsevä melu → isännöitsijä → poliisi',
            ],
            'eristys.yaml': [
                'Askeläänet: lattian alla vaimennus, matot',
                'Ikkunat: 3-lasinen = ~35 dB vaimennus',
                'Seinäeristys: mineraalivilla 50mm + levy → 10-15 dB parannus',
                'Valkoinen kohina peittää taustamelua',
            ],
        },
    },
    {
        'id': 'child_safety', 'name_fi': 'Lapsiturvallinen koti',
        'profiles': ['home'], 'priority': 'medium',
        'skills': ['myrkylliset aineet', 'pistorasia', 'portit', 'putoamissuoja', 'ensiapu'],
        'assumptions': ['Pienten lasten (0-6v) koti', 'Myrkytystietokeskus 0800 147 111'],
        'thresholds': {
            'fall_height_cm': {'value': 60, 'action': '>60 cm → suojaaide, turvaportit', 'source': 'src:CS1'},
        },
        'knowledge': {
            'vaarat.yaml': [
                'Keittiö: kuuma liesi, veitset, pesuaineet — lukkoportti',
                'Kylpyhuone: kuuma vesi (max 50°C), liukas lattia',
                'Ikkunat: putoamissuoja, ikkunalukot, turvaverkko',
                'Pistorasiat: suojatulpat',
                'Portaikot: turvaportit ylä- ja alaporteille',
            ],
            'suojaus.yaml': [
                'Turvaportit: painokiinnitys tai ruuvikiinnitys',
                'Kulmasuojat: teräviin kulmiin',
                'Kaappiturvalukot: pesuaine- ja lääkekaapit',
                'Palovaroitin + häkävaroitin joka kerrokseen',
            ],
            'ensiapu.yaml': [
                'Myrkytysepäily: soita 0800 147 111 (24h, maksuton)',
                'Palovamma: juokseva viileä vesi 20 min',
                'Tukehtuminen: alle 1v selälleen lyönnit, yli 1v Heimlich',
                'Putoaminen pään päälle: tarkkaile 24h, oksentelu → 112',
            ],
        },
    },
    {
        'id': 'pet_care', 'name_fi': 'Lemmikinhoito',
        'profiles': ['home', 'cottage'], 'priority': 'low',
        'skills': ['koira', 'kissa', 'ruokinta', 'rokotukset', 'eläinlääkäri', 'matkustus'],
        'assumptions': ['Koira tai kissa lemmikinä', 'EU-lemmikkipassi matkustukseen'],
        'thresholds': {
            'vaccination_interval_months': {'value': 12, 'action': 'Rabies + perustauteja → eläinlääkäri', 'source': 'src:PC1'},
        },
        'knowledge': {
            'hoito.yaml': [
                'Koiran ulkoilutus: vähintään 2-3 kertaa/päivä',
                'Kissan hiekkalaatikko: puhdista päivittäin',
                'Kynsien leikkuu: koira 4-6 viikon välein',
                'Hammashoito: tutkittava vuosittain',
            ],
            'terveys.yaml': [
                'Perusrokotukset: penikkatauti, parvo, rabies',
                'Punkkisuoja: huhtikuu-marraskuu, Bravecto/NexGard',
                'Madotus: aikuiset 2-4x/vuosi',
                'Vakuutus: eläinvakuutus 20-80€/kk',
            ],
            'matkustus.yaml': [
                'EU-lemmikkipassi: vaatii rabies-rokotuksen',
                'Auto: turvavaljaat tai häkki, tauot 4h välein',
                'Mökille: tarkista punkit, kyy-ensiapusetti',
                'Koirahoitola: 25-50€/yö, varaa kesälle ajoissa',
            ],
        },
    },
    {
        'id': 'budget_tracker', 'name_fi': 'Kotitalouden raha-asiat',
        'profiles': ['home'], 'priority': 'medium',
        'skills': ['kulut', 'laskut', 'budjetti', 'säästöt', 'verotus', 'tuet'],
        'assumptions': ['Suomalainen kotitalous', 'Verohallinto / Kela'],
        'thresholds': {
            'savings_rate_pct': {'value': 10, 'action': '<10% → tarkista kulut, budjetoi', 'source': 'src:BT1'},
            'rent_income_ratio_pct': {'value': 33, 'action': '>33% asumiseen → asumistuki', 'source': 'src:BT1'},
        },
        'knowledge': {
            'budjetointi.yaml': [
                '50/30/20-sääntö: välttämättömiin/vapaa/säästöön',
                'Kulutusluottoja vältettävä: korot 10-20%',
                'ASP-tili: nuoren ensiasunnon säästö, 2-4% korko',
                'Rahastosäästäminen: indeksirahasto, kuukausisäästö 50€+',
                'Hätärahasto: 3-6 kuukauden menot säästössä',
            ],
            'tuet.yaml': [
                'Yleinen asumistuki (Kela): tulosidonnainen',
                'Lapsilisä: 1. lapsi 94.88€/kk (2026)',
                'Kotitalousvähennys: 40% työkorvauksesta, max 2250€/v',
                'Opintotuet: opintoraha + asumistuki + lainatakaus',
            ],
            'verotus.yaml': [
                'Verokortti: OmaVero.fi',
                'Matkakustannusten vähennys: halvimman kulkuneuvon mukaan',
                'Työhuonevähennys: 900€ (kaavamainen)',
                'Kotitalousvähennys: siivous, remontti, hoiva — 40%',
            ],
        },
    },
    # ── FACTORY (12) ──
    {
        'id': 'production_line', 'name_fi': 'Tuotantolinjan monitorointi',
        'profiles': ['factory'], 'priority': 'high',
        'skills': ['OEE', 'sykliaika', 'pullonkaulat', 'batch-hallinta', 'tuotantosuunnitelma'],
        'assumptions': ['OEE-seuranta käytössä', 'MES/ERP-järjestelmä'],
        'thresholds': {
            'oee_target_pct': {'value': 85, 'action': '<85% → analysoi saatavuus, suorituskyky, laatu', 'source': 'src:PL1'},
            'cycle_deviation_pct': {'value': 10, 'action': '>10% poikkeama → selvitä juurisyy', 'source': 'src:PL1'},
        },
        'knowledge': {
            'oee.yaml': [
                'OEE = Saatavuus × Suorituskyky × Laatu',
                'World Class OEE: 85%+ (90% × 95% × 99.9%)',
                'Saatavuushävikki: asetukset, viat, odotusajat',
                'Suorituskykyhävikki: hidastuminen, lyhyet pysähdykset',
                'Laatuhävikki: hylkäykset, uudelleenkäsittely',
            ],
            'lean.yaml': [
                '5S: Seiri, Seiton, Seiso, Seiketsu, Shitsuke',
                'Kaizen: jatkuva parantaminen, pienet askeleet',
                'Kanban: visuaalinen ohjaus, WIP-rajoitukset',
                'Value Stream Mapping: hukan tunnistaminen',
            ],
            'scheduling.yaml': [
                'Finite capacity scheduling: todelliset kapasiteetit',
                'SMED: asetusajan minimointi',
                'Theory of Constraints: pullonkaula-analyysi',
                'Viikkosuunnitelma + päiväkohtainen hienosuunnittelu',
            ],
        },
    },
    {
        'id': 'quality_inspector', 'name_fi': 'Laadunvalvonta',
        'profiles': ['factory'], 'priority': 'high',
        'skills': ['SPC', 'Cpk', 'poikkeamaraportit', '8D', 'FMEA', 'MSA'],
        'assumptions': ['ISO 9001 -sertifioitu tuotanto', 'SPC-valvontakortit käytössä'],
        'thresholds': {
            'cpk_min': {'value': 1.33, 'action': '<1.33 → prosessi ei kyvykäs', 'source': 'src:QI1'},
            'defect_rate_ppm': {'value': 1000, 'action': '>1000 ppm → 8D-raportti', 'source': 'src:QI1'},
        },
        'knowledge': {
            'spc.yaml': [
                'X-bar/R-kortti: keskiarvon ja vaihteluvälin seuranta',
                'Cpk = min((USL-mean), (mean-LSL)) / (3*sigma)',
                'Western Electric -säännöt: hälytys poikkeavista kuvioista',
                'Control Limit vs Specification Limit: eri asia',
            ],
            'fmea.yaml': [
                'FMEA: Failure Mode and Effects Analysis',
                'RPN = Severity × Occurrence × Detection (1-10)',
                'DFMEA: tuotesuunnittelun, PFMEA: prosessin',
                'Päivitys jokaisen merkittävän muutoksen yhteydessä',
            ],
            'tools.yaml': [
                '8D-raportti: 8 askelta ongelmanratkaisuun',
                '5 Why: kysy miksi 5 kertaa → juurisyy',
                'Ishikawa (kalanruoto): syy-seuraus-kaavio, 6M',
                'Pareto: 80/20-sääntö, suurimmat ongelmat ensin',
                'MSA: gage R&R, toistettavuus + uusittavuus',
            ],
        },
    },
    {
        'id': 'shift_manager', 'name_fi': 'Vuoropäällikkö',
        'profiles': ['factory'], 'priority': 'high',
        'skills': ['vuorolista', 'ylityöt', 'resursointi', 'luovutusraportti', 'poissaolot'],
        'assumptions': ['2- tai 3-vuorojärjestelmä', 'Suomen työaikalaki'],
        'thresholds': {
            'overtime_hours_week': {'value': 8, 'action': '>8h/vko → liian korkea kuormitus', 'source': 'src:SM1'},
            'absence_rate_pct': {'value': 5, 'action': '>5% → selvitä syyt', 'source': 'src:SM1'},
        },
        'knowledge': {
            'vuorojarjestelyt.yaml': [
                '2-vuoro: aamu (06-14) + ilta (14-22)',
                '3-vuoro: aamu + ilta + yö (22-06)',
                'Ergonominen vuorokierto: aamu→ilta→yö (myötäpäivään)',
                'Yövuorolisä: TES-kohtainen, tyypillisesti 20-40%',
            ],
            'raportointi.yaml': [
                'Vuoronvaihtopalaveri: 10-15 min, tuotantostatus',
                'Luovutusraportti: tuotanto, hylky, konerikot, turvallisuus',
                'Eskalointi: pysähtyminen >30 min → ilmoitus päivystykseen',
            ],
            'tyolaki.yaml': [
                'Säännöllinen työaika: max 8h/vrk, 40h/vko',
                'Ylityö: työnantajan aloite + työntekijän suostumus, 50%/100%',
                'Vuorokautinen lepoaika: 11h yhtäjaksoista',
                'Viikottainen vapaa: 35h yhtäjaksoinen',
            ],
        },
    },
    {
        'id': 'safety_officer', 'name_fi': 'Työturvallisuuspäällikkö',
        'profiles': ['factory'], 'priority': 'high',
        'skills': ['riskiarviointi', 'läheltä_piti', 'PSA', 'kemikaaliturvallisuus', 'pelastussuunnitelma'],
        'assumptions': ['Teollisuustuotanto', 'Työturvallisuuslaki 738/2002'],
        'thresholds': {
            'near_miss_hours': {'value': 24, 'action': 'Läheltä piti käsiteltävä 24h kuluessa', 'source': 'src:SO1'},
            'accident_frequency': {'value': 10, 'action': '>10 → vakava, toimenpidesuunnitelma', 'source': 'src:SO1'},
        },
        'knowledge': {
            'riskit.yaml': [
                'Riskimatriisi: Todennäköisyys × Vakavuus = Riski (1-25)',
                'Punainen >15: välitön toimenpide, työ keskeytetään',
                'Keltainen 8-15: toimenpide suunniteltava',
                'Riskiarviointi päivitettävä vuosittain + muutosten yhteydessä',
            ],
            'psa.yaml': [
                'Suojalasit: aina koneistamossa, hionta, kemikaalit',
                'Kuulosuojaimet: >85 dB pakollinen',
                'Turvakengät: S3 (varvassuoja + naulaanastumissuoja)',
                'Hengityssuojain: FFP2/FFP3 pöly, A2 liuottimet',
            ],
            'kemikaalit.yaml': [
                'Käyttöturvallisuustiedote (SDS) saatavilla jokaiselle kemikaalille',
                'GHS-varoitusmerkit tunnistettava ennen käsittelyä',
                'Varastointi: yhteensopivuus, lukittu kaappi, valuma-allas',
                'Hätäsuihku ja silmähuuhde: 10m säteellä käyttöpaikasta',
            ],
            'lait.yaml': [
                'Työturvallisuuslaki 738/2002: työnantajan huolehtimisvelvoite',
                'Vakava tapaturma → AVI:lle 24h kuluessa',
                'Työsuojelupäällikkö: lakisääteinen',
                'Työterveyshuolto: lakisääteinen',
            ],
        },
    },
    {
        'id': 'maintenance_planner', 'name_fi': 'Kunnossapitosuunnittelija',
        'profiles': ['factory'], 'priority': 'high',
        'skills': ['PM/CM', 'varaosat', 'vikahistoria', 'MTBF/MTTR', 'RCM'],
        'assumptions': ['Tuotantokoneet', 'SAP PM tai vastaava CMMS'],
        'thresholds': {
            'pm_compliance_pct': {'value': 90, 'action': '<90% → kapasiteettipula', 'source': 'src:MP1'},
            'mtbf_hours': {'value': 500, 'action': '<500h kriittisellä koneella → juurisyy', 'source': 'src:MP1'},
        },
        'knowledge': {
            'strategiat.yaml': [
                'Korjaava (CM): korjataan kun rikkoutuu',
                'Ehkäisevä (PM): aikapohjainen huolto-ohjelma',
                'Ennakoiva (PdM): kuntomittaus (värähtely, lämpö)',
                'TPM: operaattori tekee perushuollot',
            ],
            'varaosat.yaml': [
                'Kriittiset varaosat: pidä aina varastossa, ABC-luokittelu',
                'Tilauspiste (ROP): automaattinen tilaus kun saldo alittaa',
                'Varaosabudjetti: 3-5% koneen uushankintahinnasta/vuosi',
            ],
            'kpi.yaml': [
                'MTBF: Mean Time Between Failures',
                'MTTR: Mean Time To Repair',
                'PM-suoritusaste: toteutuneet / suunnitellut × 100%',
                'Tavoite: 80/20 suunniteltu/korjaava',
            ],
        },
    },
    {
        'id': 'supply_chain', 'name_fi': 'Toimitusketjun hallinta',
        'profiles': ['factory'], 'priority': 'medium',
        'skills': ['raaka-aineet', 'varastotasot', 'tilausimpulssi', 'toimittaja-arviointi', 'JIT'],
        'assumptions': ['Valmistava teollisuus', 'ERP-järjestelmä'],
        'thresholds': {
            'inventory_turnover': {'value': 8, 'action': '<8/v → liikaa varastoa', 'source': 'src:SC1'},
            'supplier_otd_pct': {'value': 95, 'action': '<95% → vaihtoehtoinen lähde', 'source': 'src:SC1'},
        },
        'knowledge': {
            'varasto.yaml': [
                'ABC-analyysi: A 80% arvosta, 20% nimikkeistä',
                'EOQ: optimaalinen tilauseräkoko',
                'Safety stock: Z × σ × √LT',
                'FIFO: pakollinen pilaantuvalle materiaalille',
            ],
            'toimittajat.yaml': [
                'Toimittaja-auditointi vuosittain, ISO 9001',
                'Dual sourcing: vähintään 2 lähdettä kriittisille',
                'Scorecard: toimitusvarmuus, laatu, hinta, reagointi',
            ],
            'logistiikka.yaml': [
                'Incoterms 2020: DDP vs EXW',
                'Merirahti: 4-6 viikkoa Aasiasta',
                'Lentorahti: 2-5 pv, kallis',
                'Rahtikustannus: 5-15% materiaalikustannuksista',
            ],
        },
    },
    {
        'id': 'energy_manager', 'name_fi': 'Tehdasenergian hallinta',
        'profiles': ['factory'], 'priority': 'medium',
        'skills': ['tehonhallinta', 'kompressorit', 'paineilma', 'huippukulutus', 'energiakatselmus'],
        'assumptions': ['Teollisuuslaitos', 'ISO 50001 suositeltava'],
        'thresholds': {
            'power_factor_min': {'value': 0.95, 'action': '<0.95 → loistehomaksu, kompensoi', 'source': 'src:EM1'},
            'air_leak_pct': {'value': 10, 'action': '>10% vuodot → vuotokartoitus', 'source': 'src:EM1'},
        },
        'knowledge': {
            'kulutus.yaml': [
                'Huipputehon leikkaus: vuorottelu, aikataulutus → säästö',
                'Taajuusmuuttajat: 20-50% säästö ositehoilla',
                'LED-valaistus: 50-70% säästö, takaisinmaksu 1-3v',
                'Energiakatselmus: lakisääteinen >50000 MWh',
            ],
            'kompressorit.yaml': [
                'Paineilma: ~10% sähkökulutuksesta tehtaalla',
                'Vuotokartoitus ultraäänellä: 20-30% vuotaa tyypillisesti',
                'Painetason lasku 1 bar → 7% säästö',
                'Lämmön talteenotto: 90% hukkalämmöstä hyödynnettävissä',
            ],
            'optimointi.yaml': [
                'Moottorit: 60-70% sähkökulutuksesta, IE4-luokka',
                'Vapaajäähdytys talvella',
                'Hukkalämmön hyödyntäminen tilalämmitykseen',
            ],
        },
    },
    {
        'id': 'waste_handler', 'name_fi': 'Jätevirtojen hallinta',
        'profiles': ['factory'], 'priority': 'medium',
        'skills': ['jäteluokittelu', 'vaarallinen_jäte', 'kierrätysaste', 'jäteraportointi'],
        'assumptions': ['Jätelaki 646/2011', 'Ympäristölupa'],
        'thresholds': {
            'recycling_rate_pct': {'value': 70, 'action': '<70% → tehosta lajittelua', 'source': 'src:WH1'},
        },
        'knowledge': {
            'luokittelu.yaml': [
                'Jätehierarkia: ehkäise → vähennä → kierrätä → energia → loppusijoita',
                'EWC-koodi: 6-numeron koodi jokaiselle jätetyypille',
                'Biojäte: kompostiin tai biokaasulaitokseen',
                'Sekajäte: polttolaitokseen energiahyödynnykseen',
            ],
            'vaarallinen.yaml': [
                'Vaarallinen jäte: merkintä, erilliskeräys, siirtoasiakirja',
                'Jäteöljy: keräysastia, ei sekoiteta muuhun',
                'Akut: erilliskeräys, litium-akut palovaara',
                'SER: tuottajavastuu, EcoTake/Stena',
            ],
            'raportointi.yaml': [
                'YLVA-järjestelmä: vuosiraportti ELY-keskukselle',
                'Siirtoasiakirja: vaarallisen jätteen kuljetuksessa aina mukana',
                'Jätekirjanpito: laji, määrä, käsittelytapa, vastaanottaja',
            ],
        },
    },
    {
        'id': 'lab_analyst', 'name_fi': 'Laboratorioanalyytikko',
        'profiles': ['factory'], 'priority': 'medium',
        'skills': ['mittaustulokset', 'kalibrointi', 'speksit', 'GMP', 'puhdastila'],
        'assumptions': ['Tuotantolaboratorio', 'ISO/GMP-vaatimukset'],
        'thresholds': {
            'calibration_months': {'value': 12, 'action': 'Erääntynyt → mittari pois käytöstä', 'source': 'src:LA1'},
        },
        'knowledge': {
            'mittaukset.yaml': [
                'Mittausepävarmuus: ilmoitettava tulosten kanssa',
                'OOL: poikkeama speksistä → tutkinta, quarantine',
                'Trendianalyysi: havaitse muutokset ennen poikkeamaa',
            ],
            'kalibrointi.yaml': [
                'Jäljitettävyys: MIKES/VTT → tehdasstandardi → käyttömittari',
                'Kalibrointiväli: 6-12 kk riskinarvioinnin mukaan',
                'Drift-seuranta: edellisten kalibrointien vertailu',
            ],
            'puhdastila.yaml': [
                'ISO 14644-1: ISO 1 (puhtain) — ISO 9 (normaali)',
                'ISO 5: max 3520 partikkelia/m³ (≥0.5µm)',
                'Pukeutumisprotokolla: asu, maski, käsineet, kenkäsuojat',
                'Ylipaine: +15 Pa ympäristöön nähden',
            ],
        },
    },
    {
        'id': 'compliance', 'name_fi': 'Vaatimustenmukaisuus',
        'profiles': ['factory'], 'priority': 'medium',
        'skills': ['ISO_9001', 'ISO_14001', 'auditointi', 'EMAS'],
        'assumptions': ['Sertifioitu toimintajärjestelmä', 'Sisäiset ja ulkoiset auditoinnit'],
        'thresholds': {
            'audit_finding_days': {'value': 30, 'action': 'Major korjattava 30 vrk kuluessa', 'source': 'src:CO1'},
        },
        'knowledge': {
            'standardit.yaml': [
                'ISO 9001: laadunhallinta, PDCA-sykli',
                'ISO 14001: ympäristöjärjestelmä',
                'ISO 45001: työterveys ja -turvallisuus',
                'ISO 50001: energianhallinta',
            ],
            'auditointi.yaml': [
                'Sisäinen auditointi: vuosisuunnitelma, riippumattomuus',
                'Ulkoinen auditointi: sertifiointielin, 3v sykli',
                'Löydöstyypit: major, minor, OFI',
                'Korjaava toimenpide: juurisyy → korjaus → arviointi',
            ],
            'dokumentointi.yaml': [
                'Dokumentoitu tieto: menettely + tallennettu näyttö',
                'Versioiden hallinta: hyväksymisprosessi',
                'Tallennusaika: tuotekohtainen + 5-10 vuotta',
                'Sähköinen DMS: hakua ja versionhallintaa',
            ],
        },
    },
    {
        'id': 'forklift_fleet', 'name_fi': 'Trukkiliikenne ja sisälogistiikka',
        'profiles': ['factory'], 'priority': 'low',
        'skills': ['vastapainotrukki', 'lavansiirtäjä', 'lataus', 'ajoluvat', 'reitit'],
        'assumptions': ['Tehtaan sisälogistiikka', 'Trukinkuljettajan pätevyysvaatimus'],
        'thresholds': {
            'license_renewal_years': {'value': 5, 'action': 'Trukkikortti uusittava 5v välein', 'source': 'src:FF1'},
        },
        'knowledge': {
            'kalusto.yaml': [
                'Vastapainotrukki: yleisin, kantavuus 1.5-5t',
                'Lavansiirtäjä: matala, lavasiirtoihin',
                'AGV: automatisoitu, magneettinauha/laser',
            ],
            'turvallisuus.yaml': [
                'Päivittäinen tarkastus: renkaat, nesteet, jarrut',
                'Nopeusrajoitus sisätiloissa: 5-10 km/h',
                'Varoitusääni risteyksiin, peilit',
                'Kuorma: painopiste alhaalla, kallistus taaksepäin',
            ],
            'lataus.yaml': [
                'Sähkötrukin akku: 8h lataus, 8h viilennys, 8h käyttö',
                'Litium-ion: nopealataus, ei muisti-ilmiötä',
                'Latausasema: tuuletettu, sammutusväline',
            ],
        },
    },
    {
        'id': 'hvac_industrial', 'name_fi': 'Teollisuusilmastointi',
        'profiles': ['factory'], 'priority': 'medium',
        'skills': ['puhdastila-luokitus', 'ilmanvaihto', 'lämpötilakontrolli', 'kosteus', 'suodattimet'],
        'assumptions': ['Teollisuuslaitos', 'Prosessin vaatima ympäristönhallinta'],
        'thresholds': {
            'temp_deviation_c': {'value': 2, 'action': '>2°C poikkeama → hälytys', 'source': 'src:HI1'},
            'humidity_rh': {'value': '40-60', 'action': '<40% staattinen sähkö, >60% korroosio', 'source': 'src:HI1'},
        },
        'knowledge': {
            'puhdastila.yaml': [
                'HEPA H13: 99.95% erotuskyky (≥0.3µm)',
                'ISO 5: 300-500 ilmanvaihtoa/h, laminaarinen virtaus',
                'Ylipaineistus: +15 Pa huoneiden välillä',
            ],
            'ilmanvaihto.yaml': [
                'LTO-hyötysuhde: pyörivä 70-85%, levy 50-70%',
                'Vapaajäähdytys ulkoilma <15°C',
                'VAV: tarpeenmukainen ilmavirta',
                'CO2-ohjaus: <1000 ppm',
            ],
            'suodattimet.yaml': [
                'Esisuodatin (G4/M5): vaihtoväli 3-6 kk',
                'Hienosuodatin (F7/F9): vaihtoväli 6-12 kk',
                'HEPA (H13/H14): vaihtoväli 2-5 vuotta',
                'Paine-eromittaus: tukkoisuuden seuranta',
            ],
        },
    },
]


def build_core_yaml(agent):
    """Build core.yaml content for an agent."""
    profiles_str = '\n'.join(f'  - {p}' for p in agent['profiles'])
    assumptions_str = '\n'.join(f'- {a}' for a in agent['assumptions'])

    thresholds_str = ''
    for k, v in agent['thresholds'].items():
        thresholds_str += f"  {k}:\n"
        val = v['value']
        if isinstance(val, str):
            thresholds_str += f"    value: '{val}'\n"
        else:
            thresholds_str += f"    value: {val}\n"
        # Quote action to avoid YAML special chars (>, <, :, etc.)
        thresholds_str += f"    action: \"{v['action']}\"\n"
        thresholds_str += f"    source: {v['source']}\n"

    eval_qs = ''
    for k, v in agent['thresholds'].items():
        eval_qs += f"- q: Mikä on {k.replace('_', ' ')}?\n"
        eval_qs += f"  a_ref: DECISION_METRICS_AND_THRESHOLDS.{k}.value\n"
        eval_qs += f"  source: {v['source']}\n"

    return f"""header:
  agent_id: {agent['id']}
  agent_name: {agent['name_fi']}
  name_fi: '{agent['name_fi']}'
  version: 1.0.0
  last_updated: '2026-03-02'
profiles:
{profiles_str}
priority: {agent['priority']}
ASSUMPTIONS:
{assumptions_str}
DECISION_METRICS_AND_THRESHOLDS:
{thresholds_str}eval_questions:
{eval_qs}"""


def build_knowledge_yaml(filename, facts, agent_name):
    """Build a knowledge YAML file."""
    title = filename.replace('.yaml', '').replace('_', ' ').title()
    facts_str = '\n'.join(f'  - "{f}"' for f in facts)
    return f"""# {agent_name} — {title}
# Auto-generated for WaggleDance Swarm AI
title: "{title}"
agent: "{agent_name}"
facts:
{facts_str}
"""


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    created_agents = 0
    created_knowledge = 0

    for agent in AGENTS:
        aid = agent['id']

        # Create agents/{id}/core.yaml
        agent_dir = os.path.join(root, 'agents', aid)
        os.makedirs(agent_dir, exist_ok=True)
        core_path = os.path.join(agent_dir, 'core.yaml')
        with open(core_path, 'w', encoding='utf-8') as f:
            f.write(build_core_yaml(agent))
        created_agents += 1

        # Create knowledge/{id}/*.yaml
        know_dir = os.path.join(root, 'knowledge', aid)
        os.makedirs(know_dir, exist_ok=True)

        # Also copy core.yaml to knowledge
        import shutil
        shutil.copy2(core_path, os.path.join(know_dir, 'core.yaml'))

        for kfile, facts in agent['knowledge'].items():
            kpath = os.path.join(know_dir, kfile)
            with open(kpath, 'w', encoding='utf-8') as f:
                f.write(build_knowledge_yaml(kfile, facts, agent['name_fi']))
            created_knowledge += 1

        print(f"  OK {aid}: core.yaml + {len(agent['knowledge'])} knowledge files")

    print(f"\nCreated: {created_agents} agents, {created_knowledge} knowledge files")

    # Verify profile counts
    print("\nProfile counts (all 75 agents):")
    counts = {'gadget': 0, 'cottage': 0, 'home': 0, 'factory': 0}
    agents_dir = os.path.join(root, 'agents')
    for d in sorted(os.listdir(agents_dir)):
        core = os.path.join(agents_dir, d, 'core.yaml')
        if os.path.isfile(core):
            with open(core, encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if data:
                for p in data.get('profiles', []):
                    if p in counts:
                        counts[p] += 1
    for p, c in counts.items():
        print(f"  {p}: {c}")


if __name__ == '__main__':
    main()

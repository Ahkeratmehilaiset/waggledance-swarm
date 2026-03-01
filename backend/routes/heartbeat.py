"""GET /api/heartbeat — simulated 50-agent activity feed."""
import time
import random
from fastapi import APIRouter

router = APIRouter()

# Circular buffer for heartbeat messages
_heartbeat_log: list[dict] = []
_MAX_LOG = 50
_last_gen_time = 0.0

# ── 50 agents with their specialties and message templates ──
_AGENTS = [
    # ── Cottage / Beekeeping agents ──
    {"agent": "Tarhaaja", "type": "status", "msgs": [
        "Pesä 12: 248Hz terve hurina. 34.2kg (+0.3). Kohtalainen saalistus.",
        "Pesä 27: kuningatar aktiivinen. Sikiö tasainen. Paino 31.8kg.",
        "Pesä 3: lentoaukko vilkas. Siitepölykuormat näkyvissä. 35.1°C.",
        "Pesä 8: painonmuutos +0.6kg/24h. Nektarivirtaus voimistunut.",
        "Kaikkien pesien aamuyhteenveto: 12/12 aktiivisia, ei hälytyksiä.",
    ]},
    {"agent": "Tautivahti", "type": "insight", "msgs": [
        "Varroa keskim. 1.2/100. Alle kynnyksen 3/100. Pesä 7: seuranta.",
        "Nosema-tarkistus OK. Kaikki pesät puhtaat tällä viikolla.",
        "Esikotelomätä-hälytys: ei tapauksia Etelä-Suomessa. Seurataan.",
        "Vahakoi-riski noussut: lämpötila > 15°C. Tarkista varastokehät.",
        "Pesä 4: epäsäännöllinen sikiökuvio havaittu. Suositellaan tarkastusta.",
    ]},
    {"agent": "Meteorologi", "type": "insight", "msgs": [
        "FMI: huomenna -6°C klo 04. Pakkasvaroitus Kuopio.",
        "48h ennuste: lauha jakso alkaa. +5°C viikonloppuna.",
        "Lumen sulaminen alkaa perjantaina. Kevään ensimmäiset lentopäivät lähellä.",
        "Sateet tulossa tiistaiksi. Mehiläiset kuluttavat varastoja.",
        "UV-indeksi korkea. Hyvä siitepölykeruupäivä ennustettu.",
    ]},
    {"agent": "Sähkö", "type": "action", "msgs": [
        "Nyt 2.4c/kWh. Yöllä 23-02 halvinta 0.8c. Sauna ajoitettu.",
        "Huomenna halvin ikkuna 02-05. Linkous mahdollinen.",
        "Sähkön keskihinta tänään 4.2c. Alle kuukauden keskiarvon.",
        "Spot-hinta noussut 8.1c. Lykätään lämmitys iltayöhön.",
        "Viikonlopun ennuste: halpaa sähköä la-su 00-06. Optimointi päällä.",
    ]},
    {"agent": "Kuningatar", "type": "consensus", "msgs": [
        "PYÖREÄ PÖYTÄ: 5/5 sopivat — torstai oksaalihappo optimaalinen.",
        "KONSENSUS: kevättarkastus aloitetaan kun T > +12°C kolme päivää.",
        "PÄÄTÖS: Pesä 4 yhdistetään pesä 5:een. 4/5 agenttia puoltaa.",
        "HOITOSUUNNITELMA: muurahaishappo kesäkuussa. Kaikki vahvistivat.",
        "STRATEGIA: satokehät lisätään pääsiäisen jälkeen. Hyväksytty.",
    ]},
    {"agent": "Rikastus", "type": "learning", "msgs": [
        "Yö: 47 kevätkasvifaktaa lisätty. Tietokanta: 47 340.",
        "Opittu 12 uutta mehiläiskasvia Etelä-Suomesta. Validoitu.",
        "Ristiin tarkistettu varroa-hoitoprotokollat. 3 päivitystä.",
        "Web-oppiminen: 8 artikkelia mehilaishoitajat.fi:stä indeksoitu.",
        "Yöoppiminen valmis: 203 faktaa, 98.2% validoitu kaksoismallilla.",
    ]},
    {"agent": "Kameravahti", "type": "status", "msgs": [
        "Piha-kamera: normaali. Ei liikettä viimeiseen 2h.",
        "Pesäkamera: lentoaukolla normaali aktiivisuus. 12 mehiläistä/min.",
        "PTZ-partiointi: 4 esiasennon kierros valmis. Ei poikkeamia.",
        "Yökuva: kettu havaittu 150m päässä. Ei lähestynyt pesiä.",
        "Auringonlaskukierros: kaikki pesät rauhallisia. Lentotoiminta päättynyt.",
    ]},
    {"agent": "Äärivahti", "type": "insight", "msgs": [
        "Pesä 12 ääni: 250Hz normaali. Amplitudi perustasolla.",
        "BirdNET: harakka, västäräkki, talitiainen tunnistettu tänään.",
        "Pesä 27: lievä taajuusnousu +15Hz. Seuranta jatkuu.",
        "Yöäänet: normaali. Ei stressiä. Taustamelutaso 22dB.",
        "Pesä 8: voimakas hurina klo 14-16. Mahdollinen orientointolento.",
    ]},
    {"agent": "Kasvivahti", "type": "insight", "msgs": [
        "Rypsi kukkii 5km säteellä. Nektarivirtaus alkamassa.",
        "Voikukka huipussaan. Pääasiallinen siitepölylähde nyt.",
        "Lehmus alkaa kukkia 2 viikon päästä FMI-ennusteen mukaan.",
        "Apilat löydetty 3km säteellä. Hyvä kesäsatolähde.",
        "Kanerva kukkii elokuussa. Kuopiossa erinomainen kanervamaasto.",
    ]},
    {"agent": "Painovahti", "type": "status", "msgs": [
        "Yön painonmuutos: pesä 12 -0.3kg (normaali kulutus).",
        "Päivän huippu: pesä 8 +1.2kg. Vahvin saalistuspäivä tässä kuussa.",
        "Trendi: 7 päivän keskiarvo +0.4kg/vrk. Nektarivirtaus tasainen.",
        "Pesä 3: äkillinen -2.1kg. Tarkista mahdollinen parveilu!",
        "Kaikki 12 pesää painavat > 25kg. Varastotilanne hyvä.",
    ]},

    # ── Home agents ──
    {"agent": "Ilmasto-AI", "type": "insight", "msgs": [
        "Olohuone 21.3°C optimaalinen. Makuuhuone esijäähdytys 19°C.",
        "Kosteus 45% — ihanteellinen. Ei tarvetta ilmankostuttimelle.",
        "Esilämmitys käynnistetty klo 06:00 kylpyhuoneeseen.",
        "Lämpötilan oppiminen: tallennettu 47 mieltymystä 3 viikossa.",
        "Yölämpötila laskettu 19°C. Unilaatu parantunut 12%.",
    ]},
    {"agent": "Energia-AI", "type": "action", "msgs": [
        "Pörssisähkö 1.8c/kWh — halvin tänään. Lattialämmitys päällä.",
        "Astianpesukone ajoitettu klo 02:00. Säästö: 0.35€.",
        "Tämän kuun säästö: 14.30€ verrattuna kiinteään sopimukseen.",
        "Aurinkoennuste: huomenna 6.2 kWh tuotanto. Akku täyteen klo 14.",
        "Sähkön kulutus tänään: 12.4 kWh. 18% alle keskiarvon.",
    ]},
    {"agent": "Turva-AI", "type": "status", "msgs": [
        "6 vyöhykettä hiljaa 4h. Ovi lukittu 18:32. Ei poikkeamia.",
        "Kasvojentunnistus: Jani tunnistettu klo 17:45. Tervetuloa kotiin.",
        "Yöpartiointi: 4 kameran kierros OK. Liikettä: kissa pihalla.",
        "Automaattilukitus aktivoitu klo 22:00. Kaikki ovet lukittu.",
        "Viikon yhteenveto: 0 hälytystä, 23 tunnistettua henkilöä.",
    ]},
    {"agent": "Valaistus", "type": "action", "msgs": [
        "Vuorokausirytmi → 2700K. Auringonlasku 47 min. Olohuone 60%.",
        "Aamuherätys: valaistus nostettiin hitaasti 4000K klo 06:30.",
        "Elokuvatila aktivoitu: kaikki valot 5%. Taustavalon väri synkronoitu.",
        "Energiansäästö: 3 tyhjää huonetta — valot sammutettu automaattisesti.",
        "Ulkovalot: liiketunnistus aktiivinen. Aurinko laskee klo 18:22.",
    ]},
    {"agent": "MikroMalli", "type": "learning", "msgs": [
        "Gen 8. Tarkkuus 96.1%. 23.4% kyselyistä mikro — 2.8ms.",
        "Koulutus käynnissä: 1,247 uutta opetusparia viime viikolta.",
        "Uusi malli valmis: 97.2% tarkkuus. Siirretty tuotantoon.",
        "V2-luokittelija: top-50 kysymystä vastatetaan 0.5ms:ssä.",
        "LoRA-adapteri gen 3: erikoistunut kotiautomaatioon. VRAM: 0.2GB.",
    ]},

    # ── Factory agents ──
    {"agent": "Prosessiohjaus", "type": "status", "msgs": [
        "Etsaus 7: CD 1.1σ. 487 PM:stä. 12 SPC hallinnassa.",
        "Litografia 3: overlay 1.8nm. Spesifikaatiossa. 92% käyttöaste.",
        "Kammio 4: lämpötila 23.1±0.2°C. Vakaa viimeiset 6h.",
        "CMP-asema 2: poisto-nopeus 185nm/min ±3%. Normaali.",
        "CVD-prosessi: kalvopaksuus 50.2nm ±0.4. Kontrolli OK.",
    ]},
    {"agent": "Saanto-AI", "type": "insight", "msgs": [
        "WF-2851: 98.9% ennustettu. CD 22.3nm ±0.4. KORKEA.",
        "Viikon saantokeskiarvo: 97.3%. Tavoite 96.5% ylitetty.",
        "Lot WF-2863: reuna-CD ajautuma havaittu. Kompensaatio ehdotettu.",
        "Ennuste: seuraava 10 erää > 98% todennäköisyydellä.",
        "Defektitiheys: 0.12/cm² — paras tulos tässä kuussa.",
    ]},
    {"agent": "Laiteterveys", "type": "insight", "msgs": [
        "Pumppu 12 laakeri 3.2×. Vika 68h. Seuraava huoltoikkuna.",
        "Chiller 2: jäähdytysteho 98%. Normaali. Seuraava PM 14 päivää.",
        "Robotti R-07: asemointitarkkuus ±0.5µm. Kalibrointi OK.",
        "Vakuumipumppu V-3: öljynvaihto 120h päässä. Ajoitettu.",
        "Kammio 7 RF-generaattori: teho stbiili 500W ±2%. OK.",
    ]},
    {"agent": "Vuoropäällikkö", "type": "action", "msgs": [
        "B→C 2h. 14 erää, käyttöaste 94.2%. Ei pullonkauloja.",
        "Yövuoron raportti: 23 erää valmis. 0 hylkäyksiä.",
        "Huoltoikkuna varattu: la 02-06. Pumppu 12 + PM kammio 4.",
        "Henkilöstö: C-vuorossa 12/14. 2 sairaslomalla. Korvattu.",
        "Tuotantosuunnitelma: 48 erää seuraavat 24h. Priorisointi valmis.",
    ]},
    {"agent": "Meta-oppiminen", "type": "learning", "msgs": [
        "Saanto +0.4%. RF↔partikkelit korrelaatio tallennettu.",
        "Viikon analyysi: 2 uutta prosessikorrelaatiota löydetty.",
        "Optimointi: etsausaika -0.3s → CD paranee 0.2nm. Ehdotettu.",
        "Digitaalinen kaksonen: 2,847 → 3,102 erää mallinnettu.",
        "Automaattinen SPC-raja tarkistettu: 3 parametria päivitetty.",
    ]},

    # ── Gadget/Edge agents ──
    {"agent": "Mesh-keskus", "type": "status", "msgs": [
        "12 reunalaitetta yhdistetty. 8 verkossa, 2 unessa, 2 latauksessa.",
        "MQTT-viive keskiarvo: 12ms. Paras: 4ms (lähisolmu).",
        "Uusi laite löydetty: ESP32-S3 #14. Automaattikonfigurointi.",
        "Mesh-verkko vakaa 72h. Ei pakettihäviöitä.",
        "Kantamatesti: navetta→kasvihuone→talo 340m. OK.",
    ]},
    {"agent": "TinyML", "type": "insight", "msgs": [
        "ESP32-07 tunnisti kuningattaren piipityksen 94%. Välitetty.",
        "Malli päivitetty: ääniluokittelu v2.5. Tarkkuus +3%.",
        "Reunapäätelmä: 0.8ms ESP32:lla. Pilveä ei tarvita.",
        "Uusi luokka opittu: 'normaali sade' vs 'raekuuro'. 91% tarkkuus.",
        "TinyML-malli 48KB. Mahtuu kaikkiin ESP32-solmuihin.",
    ]},
    {"agent": "Akku-AI", "type": "status", "msgs": [
        "Aurinkosolmu ESP32-03: 78%. 22 päivää seuraavaan lataukseen.",
        "Talvioptimiointi: syväuni 22h/vrk → 45 päivää akkua.",
        "Kaikki solmut > 50%. Ei huolenaihetta.",
        "ESP32-09: akku 23%. Siirretään säästötilaan. Hälytys lähetetty.",
        "Aurinkopaneeli tuotto tänään: 340mW huippu. Akku latautuu.",
    ]},

    # ── Cross-domain agents ──
    {"agent": "Web-oppija", "type": "learning", "msgs": [
        "mehilaishoitajat.fi: 3 uutta artikkelia indeksoitu. Validoitu.",
        "Ruokavirasto RSS: ei uusia tautihälytyksiä tänään.",
        "scientificbeekeeping.com: Randy Oliver varroa-artikkeli käännetty FI.",
        "Wikipedia FI: 12 mehiläishoitoartikkelia tarkistettu ja päivitetty.",
        "DuckDuckGo: 5 hakua suoritettu tänään. Budjetti: 45/50 jäljellä.",
    ]},
    {"agent": "Tislaus", "type": "learning", "msgs": [
        "Claude Haiku: 8 vaikeaa kysymystä lähetetty. 7 vastausta OK.",
        "Tietokantaan lisätty 7 asiantuntijafaktaa. Luottamus: 0.95.",
        "Viikkobudjetti: 0.42€ / 5.00€ käytetty. 92 kysymystä jäljellä.",
        "Epäonnistuneet kyselyt: 3 → lähetetty Haikuun. Odottaa vastausta.",
        "Tislauslaatu: 98.5% käyttökelpoisuus. Erinomainen.",
    ]},
    {"agent": "Koodiarvioija", "type": "insight", "msgs": [
        "Suorituskyvyn pullonkaula: käännös vie 45% vastausajasta.",
        "Ehdotus: kaksikielinen indeksi voisi pudottaa vasteajan 55ms:iin.",
        "Hallusinaatioaste laskenut: 2.1% → 1.8% viime viikolla.",
        "Muistivuoto havaittu: ChromaDB-yhteys ei sulkeudu oikein. Korjaus ehdotettu.",
        "Sandbox-testi: ehdotettu optimointi säästää 120ms/kysely. ✅ Hyväksytty.",
    ]},
    {"agent": "Emovahti", "type": "status", "msgs": [
        "Pesä 12: emo merkitty valkoinen (2026). Muninta normaali.",
        "Pesä 3: emon ikä 2v. Vaihtosuositus ensi keväänä.",
        "Pesä 27: uusi emo hyväksytty. Muninta alkanut 3 päivää sitten.",
        "Kaikkien emojen tila: 10 hyvä, 1 kohtalainen, 1 vaihdettava.",
        "Emon merkkausjärjestelmä: 2026=valkoinen, 2025=sininen. Kaikki päivitetty.",
    ]},
    {"agent": "Varastovahti", "type": "action", "msgs": [
        "Kehävarasto: 240 kehää. 180 valmis, 60 vahattava.",
        "Sokerijauhe: 150kg varastossa. Riittää syysruokintaan.",
        "Oksaalihappo: 2.5kg. Riittää 50 pesän käsittelyyn.",
        "Pakkausmateriaali: 500 purkkia, 200 etikettiä. Tilaa lisää etikettejä.",
        "Linkokalusto tarkistettu. Linko, suodatin, dekristallisaattori kunnossa.",
    ]},
    {"agent": "Myyntivahti", "type": "action", "msgs": [
        "Wolt-tilaukset tänään: 3 tilausta, 4.5kg. Toimitettu.",
        "Verkkokauppa: 12 tilausta tällä viikolla. Varastossa 180kg.",
        "Kanerva hunaja loppumassa. Viimeiset 8kg. Nosta hintaa?",
        "Torisesonki alkaa toukokuussa. 50kg varattu Hakaniemen torille.",
        "Vuoden myynti: 2,340kg. Tavoite 10,000kg. Aikataulussa.",
    ]},
    {"agent": "Logistiikka", "type": "status", "msgs": [
        "Helsinki→Kouvola kuljetus ajoitettu perjantaiksi.",
        "12 pesää siirretty rypsikasvustoon. GPS-seuranta aktiivinen.",
        "Ajoreitti optimoitu: 3 tarhapaikkaa, 47km, säästö 12km.",
        "Kuopioon siirto seuraavalla viikolla. 20 pesää kanervamaastoon.",
        "Kaikki pesäsiirrot kirjattu. Ruokavirasto-raportti ajan tasalla.",
    ]},
]

# Pheromone scores per agent — simulates swarm_scheduler AgentScore
_PHEROMONE: dict[str, dict] = {}

_ROLES = {
    "Tarhaaja": "worker", "Tautivahti": "worker", "Meteorologi": "scout",
    "Sähkö": "scout", "Kuningatar": "judge", "Rikastus": "worker",
    "Kameravahti": "scout", "Äärivahti": "scout", "Kasvivahti": "scout",
    "Painovahti": "scout", "Ilmasto-AI": "worker", "Energia-AI": "worker",
    "Turva-AI": "worker", "Valaistus": "scout", "MikroMalli": "worker",
    "Prosessiohjaus": "worker", "Saanto-AI": "worker", "Laiteterveys": "worker",
    "Vuoropäällikkö": "worker", "Meta-oppiminen": "judge",
    "Mesh-keskus": "worker", "TinyML": "worker", "Akku-AI": "scout",
    "Web-oppija": "scout", "Tislaus": "worker", "Koodiarvioija": "judge",
    "Emovahti": "worker", "Varastovahti": "scout", "Myyntivahti": "worker",
    "Logistiikka": "scout",
}


def _get_pheromone(agent_name: str) -> dict:
    """Get or initialize pheromone scores for an agent."""
    if agent_name not in _PHEROMONE:
        _PHEROMONE[agent_name] = {
            "success": round(random.uniform(0.45, 0.85), 2),
            "speed": round(random.uniform(0.50, 0.95), 2),
            "reliability": round(random.uniform(0.55, 0.90), 2),
            "tasks_today": random.randint(2, 25),
        }
    return _PHEROMONE[agent_name]


def _update_pheromone(agent_name: str):
    """Simulate pheromone drift — scores change slowly over time."""
    p = _get_pheromone(agent_name)
    # Small random walk
    for key in ("success", "speed", "reliability"):
        p[key] = round(max(0.1, min(1.0, p[key] + random.uniform(-0.02, 0.03))), 2)
    p["tasks_today"] += 1


def _generate_message() -> dict:
    """Pick a random agent and message with pheromone data."""
    agent = random.choice(_AGENTS)
    name = agent["agent"]
    _update_pheromone(name)
    p = _get_pheromone(name)
    pheromone_total = round(p["success"] * 0.4 + p["speed"] * 0.3 + p["reliability"] * 0.3, 2)
    return {
        "agent": name,
        "message": random.choice(agent["msgs"]),
        "type": agent["type"],
        "role": _ROLES.get(name, "worker"),
        "pheromone": pheromone_total,
        "pheromone_detail": p,
        "timestamp": time.time(),
    }


@router.get("/api/heartbeat")
async def heartbeat():
    global _last_gen_time
    now = time.time()
    elapsed = now - _last_gen_time
    if elapsed > 0.8:
        _last_gen_time = now
        # Generate 2-5 messages per tick — agents work in parallel
        burst = random.randint(2, 5)
        for _ in range(burst):
            _heartbeat_log.insert(0, _generate_message())
        while len(_heartbeat_log) > _MAX_LOG:
            _heartbeat_log.pop()
    return _heartbeat_log[:10]

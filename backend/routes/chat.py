"""POST /api/chat — chat endpoint (stub with canned responses + YAML knowledge).

Layer 0: Date/time/system queries (both languages)
Layer 1: Hand-crafted keyword responses (47 entries, Finnish morphology-aware)
Layer 2: Auto-loaded YAML eval_questions (~3600 Q&A pairs from 50 agents)
Layer 3: Fallback messages
"""
import json
import logging
import math
import os
import random
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger("waggledance-chat")

# ---------------------------------------------------------------------------
# Seasonal Guard — deterministic season check on answers
# ---------------------------------------------------------------------------

_SEASONAL_GUARD = None

def _get_seasonal_guard():
    global _SEASONAL_GUARD
    if _SEASONAL_GUARD is not None:
        return _SEASONAL_GUARD
    try:
        import sys
        project_root = str(Path(__file__).resolve().parent.parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from core.seasonal_guard import SeasonalGuard
        _SEASONAL_GUARD = SeasonalGuard()
        log.info("SeasonalGuard loaded: %d rules", _SEASONAL_GUARD.rule_count)
    except Exception as e:
        log.warning("SeasonalGuard not available: %s", e)
    return _SEASONAL_GUARD


def _apply_seasonal_guard(answer: str) -> str:
    """Annotate answer with seasonal warnings if needed. <0.1ms."""
    guard = _get_seasonal_guard()
    if guard is None:
        return answer
    return guard.annotate_answer(answer)


class ChatRequest(BaseModel):
    message: str = ""
    lang: str = "auto"


router = APIRouter()

# ---------------------------------------------------------------------------
# Language detection (lightweight, no dependencies)
# ---------------------------------------------------------------------------

_FI_CHARS = set("äöåÄÖÅ")
_FI_SUFFIXES = ("ssa", "ssä", "lla", "llä", "sta", "stä", "lle", "lta", "ltä",
                "een", "iin", "nko", "nkö", "ista", "istä")
_EN_WORDS = {"the", "is", "are", "was", "were", "have", "has", "been", "will",
             "would", "could", "should", "what", "how", "when", "where", "which",
             "that", "this", "with", "from", "about", "into", "does", "not",
             "you", "your", "can", "tell", "know", "please", "yes", "yeah",
             "do", "did", "don", "who", "why", "there", "they", "them",
             "its", "it", "much", "many", "some", "any", "my", "me"}
# Single-word tokens that are unambiguously English (for 1-word messages)
_EN_SOLO = {"hi", "hello", "hey", "howdy", "sup", "yo", "thanks", "cheers",
            "yes", "no", "okay", "ok", "sure", "bye", "goodbye", "nope"}


def _detect_language(text_lower: str) -> str:
    """Fast language detection: 'fi', 'en', or 'fi' (default)."""
    # Level 0: Unambiguous single English words
    stripped = text_lower.strip().rstrip("!?.")
    if stripped in _EN_SOLO:
        return "en"
    # Level 1: Finnish characters → Finnish (almost certain)
    if _FI_CHARS & set(text_lower):
        return "fi"
    # Level 2: Word scoring
    words = set(re.findall(r"[a-zäöå]+", text_lower))
    en_score = len(words & _EN_WORDS)
    fi_score = 0
    for w in words:
        for sfx in _FI_SUFFIXES:
            if w.endswith(sfx) and len(w) > len(sfx) + 2:
                fi_score += 1
                break
    # Even 1 English word with no Finnish evidence → English
    if en_score >= 1 and fi_score == 0:
        return "en"
    if en_score > fi_score and en_score >= 2:
        return "en"
    return "fi"  # default to Finnish


_FALLBACK_EN = [
    "I don't have a specific answer for that yet. In stub mode, I answer from pre-loaded knowledge. "
    "Try asking about specific domain topics! 🐝",
    "Hmm, that's outside my current knowledge base. I'm running in stub mode with 1,348+ facts. "
    "Start the full HiveMind (python main.py) for LLM-powered answers.",
    "I'm WaggleDance — a multi-agent AI system. In stub mode, my answers come from YAML knowledge. "
    "Try specific questions, or start the full system for deeper answers.",
    "Good question! I don't have that in my current knowledge base. "
    "The full HiveMind with 50 agents and ChromaDB would give you a better answer.",
]

# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------


def _strip_diacritics(text: str) -> str:
    """Remove diacritics: ä→a, ö→o, å→a etc. for fallback matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _match(msg_lower: str, keywords: list[str]) -> bool:
    """Two-pass: direct substring then diacritics-stripped fallback."""
    msg_ascii = _strip_diacritics(msg_lower)
    for kw in keywords:
        if kw in msg_lower:
            return True
        if _strip_diacritics(kw) in msg_ascii:
            return True
    return False


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens >= 3 chars, stripping punctuation."""
    return {w for w in re.findall(r"[a-zäöåà-ÿ0-9]+", text.lower()) if len(w) >= 3}


# Common Finnish question/grammar words — skip these in YAML matching score
_STOP_WORDS = {
    # Question words
    "mikä", "mika", "mitä", "mita", "miten", "milloin", "missä", "missa",
    "miksi", "kuka", "kenelle", "onko", "voiko", "saako", "mitkä", "mitka",
    "mihin", "mistä", "mista", "kuinka", "paljonko",
    # Verbs / grammar
    "ovat", "ole", "olla", "eivät", "eiva", "että", "etta",
    "tämä", "tama", "tämän", "taman", "tätä", "tata",
    "liittyy", "koskee", "huomioidaan", "tehdään", "tehdaan",
    "agentin", "agentti",
    # Generic action verbs appearing in many agents
    "toimenpide", "toiminta", "havainto", "sääntö", "saanto",
    "tarkista", "tarkistetaan", "arvo", "arvon",
}


def _fix_double_utf8(text: str) -> str:
    """Fix double-encoded UTF-8 (Ã¤ → ä). Returns original if not double-encoded."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _fix_yaml_strings(obj):
    """Recursively fix double-encoded UTF-8 in YAML data."""
    if isinstance(obj, str):
        return _fix_double_utf8(obj)
    elif isinstance(obj, dict):
        return {_fix_double_utf8(k) if isinstance(k, str) else k: _fix_yaml_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_fix_yaml_strings(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Layer 1: Hand-crafted keyword responses (tested, Finnish morphology-aware)
# ---------------------------------------------------------------------------

_RESPONSES: list[tuple[list[str], str]] = [
    # --- Greetings & meta ---
    (["moi!", "hei!", " moi", "terve", "morjens"],
     "Moi! Olen WaggleDance — paikallinen tekoälyavustaja. Kysy mitä vain! 🐝"),
    (["kiitos", "thanks", "thx"],
     "Ole hyvä! Kysy lisää jos tulee mieleen."),
    (["kuka olet", "mikä olet", "esittele"],
     "Olen WaggleDance, paikallinen tekoälyjärjestelmä. Muistini kasvaa joka päivä."),

    # --- Diseases & pests ---
    (["varroa"],
     "Varroa-punkin torjunnassa oksaalihappo on tehokas syyshoito. Käsittele pesät lokakuussa sikiöttömänä aikana. "
     "Muurahaishappo toimii kesällä. Seuraa pudotusta aluslaudalta — kynnys on 1 punkki/vrk."),
    (["esikotelomät", "esikotelomäd"],
     "Esikotelomätä (AFB) on ilmoitettava tauti! Oireet: limainen, haiseva sikiö ja uponnut peite. "
     "Tikku-testi: lima venyy 2-3 cm. Ilmoita heti Ruokavirastoon."),
    (["toukkamät", "toukkamäd", " efb", "european foulbrood"],
     "Eurooppalainen toukkamätä (EFB): toukat kuolevat ennen koteloitumista. Oireet: epäsäännöllinen sikiökuvio, "
     "kellertävät toukat. Usein korjaantuu vahvalla emolla ja hyvällä satokauden ravinnolla."),
    (["nosema"],
     "Nosema ceranae on mehiläisten suolistoloinen. Oireet: ripulijäljet pesän edessä ja heikko kehitys keväällä. "
     "Ennaltaehkäisy: kuivat ja hyvin tuuletetut talvehtimistilat, nuori emo."),
    (["pesäkuoriainen", "hive beetle"],
     "Pieni pesäkuoriainen (Aethina tumida) ei ole vielä Suomessa, mutta leviää Etelä-Euroopassa. "
     "Ilmoita heti Ruokavirastoon jos epäilet havaintoa."),
    (["herhiläi", "vespa velutina", "vespa crabro"],
     "Aasianherhiläinen (Vespa velutina) on uhka mehiläisille. Ei vielä Suomessa, mutta seurataan. "
     "Eurooppalainen herhiläinen (Vespa crabro) on yleinen mutta harvoin vakava ongelma pesille."),
    (["vahakoi"],
     "Vahakoi (Galleria mellonella) tuhoaa varastoidut kehät. Säilytä kehät viileässä ja valoisassa. "
     "Rikkikaasukäsittely tai pakastus -20 °C 48 h tuhoaa toukat."),

    # --- Colony management ---
    (["emottaminen", "uusi emo", "vaihda emo", "emon vaihto", "vaihtaa emo"],
     "Emon vaihto: poista vanha emo, odota 24 h, lisää uusi emo häkissä. Vapauta 3 päivän jälkeen kun pesä hyväksyy. "
     "Paras aika: satokauden jälkeen heinä-elokuussa."),
    (["merkitsemi", "merkintä", "värikoodi", "värikood"],
     "Emomerkintä vuosivärit: valkoinen 1/6, keltainen 2/7, punainen 3/8, vihreä 4/9, sininen 5/0. "
     "Vuonna 2026 = valkoinen, 2025 = sininen."),
    (["kuningatar", "kuningattar", "emosta", "emolla", "emoni", "emoa"],
     "Kuningattarella on 5 silmää: 2 suurta verkkosilmää ja 3 pientä pistesilmää. Emo elää 2-5 vuotta. "
     "Merkitse emot värikoodeilla: 2026 = valkoinen, 2025 = sininen."),
    (["parveilu", "parveil"],
     "Parveilu on mehiläisten luontainen lisääntymistapa. Estä: anna tilaa, pidä nuori emo, riko emocellien alkuja. "
     "Parveiluvietti vahvimmillaan touko-kesäkuussa."),
    (["yhdistä", "yhdistäm"],
     "Pesien yhdistäminen: aseta sanomalehti pesien väliin ja pinoa toinen päälle. "
     "Mehiläiset puraisevat lehden läpi 1-2 päivässä ja yhdistyvät rauhallisesti. Poista heikompi emo ensin."),
    (["jakoparv", "jakaminen"],
     "Jakoparvi tehdään siirtämällä 3-5 kehää sikiöineen ja mehiläisineen uuteen pesään. "
     "Anna emocelli tai pariutunut emo. Siirrä uusi pesä vähintään 3 km tai sulje 3 päiväksi."),

    # --- Seasonal ---
    (["kevät", "kevää", "kevättarkast"],
     "Kevättarkastuksessa tarkista: emon muninta, ravintotilanne (vähintään 5 kg), pesän vahvuus ja puhtaus. "
     "Älä avaa pesää alle +12 °C. Supista pesä tarvittaessa vastaamaan vahvuutta."),
    (["talveht", "talvi"],
     "Talvehtimisen onnistuminen riippuu: riittävä ravinto (15-20 kg), matala varroa-taso, nuori emo, "
     "hyvä tuuletus. Syötä sokeriliuosta (3:2) syys-lokakuussa."),
    (["satokausi", "satokehä"],
     "Satokausi Suomessa: kesä-heinäkuu. Lisää satokehiä ajoissa — mehiläiset tarvitsevat tilaa. "
     "Kun kehä 80 % peitetty, linkoa. Älä linkoa sikiökehiä."),
    (["syyshoito", "syksy"],
     "Syyshoito-ohjelma: 1) Linkoa viimeinen sato elokuussa, 2) Varroa-hoito heti perään, "
     "3) Ruokinta syyskuussa, 4) Supista pesä, 5) Hiirisuoja lentoaukkoon."),
    (["linkous", "linkoa", "linko"],
     "Linkous: kehät auki veitsellä tai kuorintahaarukalla. Lingon kierrosnopeus tasaisesti ylös. "
     "Siivilöi hunaja 200 µm siivilän läpi. Kosteus alle 20 % — mittaa refraktometrillä."),

    # --- Products (wolt/myynti before hunaja) ---
    (["wolt", "myynti", "myydä", "myy "],
     "Hunajanmyyntikanavat: Wolt, verkkokauppa, torit, suoramyynti. Lajihunajan kilohinta 12-25 €. "
     "Muista elintarvikelainsäädäntö ja pakkausmerkinnät."),
    (["hunaja"],
     "Hyvä hunajavuosi tuottaa 30-50 kg per pesä Suomessa. Lajihunajat (rypsi, kanerva, lime) ovat arvokkaampia. "
     "Kosteus alle 18 % on paras laatu, alle 20 % hyväksyttävä."),
    (["propolis"],
     "Propolis on mehiläisten keräämää puiden pihkaa. Antimikrobinen aine, jota pesä käyttää tiivisteenä. "
     "Kerää raaputtamalla kehälistoista tai propolisverkolla. Liuota 70 % etanoliin."),
    (["siitepöly", "pollen"],
     "Siitepöly kerätään siitepölyloukulla lentoaukon edessä. Kuivaa heti +40 °C, säilytä pakkasessa. "
     "Arvokas ravintolisä. Muista: jätä pesälle riittävästi omaan käyttöön!"),
    (["mehiläisvaha"],
     "Mehiläisvaha sulaa 62-65 °C. Puhdista: sulata vedessä, siivilöi, anna jähmettyä. "
     "Käyttö: uudet vahaliuskat, kosmetiikka, kynttilät. 10 kg hunajaa = ~1 kg vahaa."),

    # --- Biology ---
    (["silmä", "silmät", "silmää"],
     "Mehiläisellä on 5 silmää: 2 suurta verkkosilmää (tuhansia linssejä) sivuilla ja 3 pistesilmää (ocellia) "
     "pään päällä. Verkkosilmät näkevät UV-valoa, pistesilmät havaitsevat valon voimakkuuden."),
    (["siipi", "lentä", "lento"],
     "Mehiläisen siivet lyövät ~200 kertaa sekunnissa. Lentonopeus 24 km/h, kantama ~3 km pesältä. "
     "Kuormassa (nektari/siitepöly) nopeus laskee. Siipiä 2 paria, kiinnittyvät toisiinsa koukuilla."),
    (["kuhnuri", "drone"],
     "Kuhnuri on koirasmehiläinen. Tehtävä: paritella emon kanssa. Ei pistintä, ei kerää ruokaa. "
     "Pesässä 200-2000 kuhnuria kesällä. Syksyllä työmehiläiset ajavat kuhnurit ulos."),
    (["tanssi", "waggle", "tanssikieli"],
     "Mehiläisen tanssikieli: pyörötanssi = ruokaa lähellä (<100 m), viivatanssi (waggle) = suunta + etäisyys. "
     "Tanssin kulma kertoo suunnan suhteessa aurinkoon. Tanssi kehällä pesän sisällä."),
    (["elinikä", "elinkaari"],
     "Työmehiläinen elää kesällä 4-6 viikkoa, talvimehiläinen 4-6 kuukautta. "
     "Kuningatar elää 2-5 vuotta. Kuhnuri elää muutaman kuukauden — kunnes parittelee tai ajetaan ulos."),
    (["rakenne", "anatomia"],
     "Mehiläisen keho: pää (silmät, tuntosarvet, suuosat), keskiruumis (jalat, siivet), takaruumis (pistin, vahasarvet, hunajamaha). "
     "Hunajamaha vetää ~40 mg nektaria. Pistin on vain työmehiläisillä ja emolla."),

    # --- Equipment & practical ---
    (["langstroth", "dadant", "pesätyyppi", "pesätyyp"],
     "Suomessa yleisimmät pesätyypit: Langstroth (kansainvälinen standardi) ja Farrar (korkea kehä). "
     "Pesä koostuu pohjasta, sikiöosasta (1-2 kehälaatikkoa), satokehistä ja kannesta."),
    (["ruokint", "ruokin", "sokeri", "syöttö", "syötä"],
     "Syysruokinta: sokeriliuos 3:2 (3 kg sokeria, 2 l vettä). Anna 15-20 kg per pesä. "
     "Syötä syyskuussa, jotta mehiläiset ehtivät kääntää ja peittää varastot ennen talvea."),
    (["oksaalihappo", "muurahaishappo"],
     "Oksaalihappo: tehokas sikiöttömänä aikana (loka-marraskuu), tihkutus tai höyrytys. "
     "Muurahaishappo: käytetään satokauden jälkeen, tehoaa myös peitettyyn sikiöön. 60 % liuos, 20 ml/kehäväli."),
    (["suojaus", "pistos", "suojapuku"],
     "Perussuojaus: mehiläishattu/huntu, pitkähihaiset vaatteet, hanskat. Täysi suojapuku aloittelijoille. "
     "Savutin rauhoittaa mehiläiset — käytä kuivaa puuta tai pahvia."),
    (["savutin", "savua"],
     "Savutin rauhoittaa mehiläiset: savu laukaisee ruokintarefleksin. Käytä viileää savua. "
     "Hyvää polttoainetta: kuiva puu, pahvi, kuivat neulaset. Älä käytä synteettisiä materiaaleja."),

    # --- Cross-domain agent answers (YAML eval_questions use English metric names) ---
    (["sähkötyö", "luvanvarai", "vikavirtasuoj"],
     "[Sähköasentaja] Luvanvaraiset sähkötyöt kuuluvat rekisteröidylle sähköurakoitsijalle. "
     "Maallikkotöitä: sulakkeen vaihto, valaisimen kytkentä (rajoitetusti). Pistorasian asennus → ei sallittu."),
    (["palovaroitin", "häkävaroitin", "sammutin"],
     "[Paloesimies] Palovaroitin testataan kuukausittain painikkeesta. Vaihda 10 v välein. "
     "Sammutin max 15 m jokaisesta pisteestä. CO-hälytin: >50 ppm → soi, >100 ppm → evakuoi, soita 112."),
    (["lumikuorm", "kattokuorm"],
     "[Pihavahti] Lumikuorman raja-arvo tyypillisesti 150 kg/m². Tarkkaile lunta katolla "
     "erityisesti märän lumen ja rännistöjen kohdalla. Poista ennen raja-arvon ylitystä."),
    (["kalkkisiki", "chalkbrood"],
     "[Tautivahti] Kalkkisikiö (Ascosphaera apis): >10 % kehyksistä kalkkisikiöitä → vaihda emo, "
     "paranna ilmanvaihtoa, poista pahimmat kehykset. Yleinen mutta harvoin tuhoisa."),
    (["alamitta", "kalastus", "kalakiintiö"],
     "[Kalastusopas] Hauen alamitta 40 cm, kuhan 42 cm (voi vaihdella alueittain). "
     "Tarkista aina paikalliset kalastusmääräykset ja luvat."),
    (["sauna", "kiuas", "löyly"],
     "[Saunamajuri] Saunan suosituslämpötila 70-90 °C. Kiuaskivet tarkistetaan 1-2 vuoden välein. "
     "Savuhormi tarkistettava ennen käyttöä. Nesteytys: 0.5 l per saunomiskerta. Jäähdyttele rauhassa."),

    # --- Finnish context ---
    (["mökki", "mökil", "cottage"],
     "Mökkitarhauksessa huomioi naapurit — sijoita pesät niin, että lentoaukot osoittavat poispäin piha-alueelta. "
     "Suositeltu etäisyys: vähintään 10 m pihasta, mielellään aidan tai pensasaidan takana."),
    (["sähkön hinta", "pörssisähkö", "spotti", "spot-hinta", "sähkön spot"],
     "Sähkön spot-hinta vaihtelee tunneittain. Käytä lämmityslaitteita halvimpien tuntien aikana. "
     "Tarkista hinnat: porssisahko.net. Yöllä ja viikonloppuisin usein halvempaa."),
    (["lämpötila", "ilmasto", "ennuste"],
     "Mehiläiset lentävät yli +12 °C. Parhaat keruupäivät: +18-25 °C, aurinkoinen, tyyni. "
     "Sadepäivinä pesä kuluttaa varastoja. Pitkä kylmäjakso keväällä = ruokintariski."),
    (["kukka", "mesikasv", "satokasv"],
     "Suomen tärkeimmät mesikasvit: rypsi/rapsi (kesäkuu), vadelma (heinäkuu), kanerva (elokuu), "
     "apila (kesä-heinäkuu), lehmus/lime (heinäkuu). Mesimuistio auttaa satokausien suunnittelussa."),
    (["lainsäädäntö", "rekisteri", "laki "],
     "Mehiläistenpidon aloitus: ilmoitus kuntaan ja Ruokavirasto-rekisteriin. "
     "Pesien sijoituksesta ei ole valtakunnallista etäisyyssääntöä, mutta kunnat voivat säätää paikallisesti."),

    # --- WaggleDance system ---
    (["agentti", "agent", "swarm"],
     "WaggleDancessa toimii 50+ erikoistunutta agenttia: beekeeper, disease_monitor, meteorologist jne. "
     "Agentit keskustelevat Round Table -istunnoissa ja oppivat toisiltaan."),
    (["oppimi", "oppii", "muisti", "chromadb"],
     "WaggleDance oppii jatkuvasti: YAML-tiedostot, keskustelut, Round Table, verkko. "
     "Muisti kasvaa ~800 faktaa/yö. Kaikki tallennetaan ChromaDB-vektoritietokantaan."),
    (["round table", "pyöreä pöytä"],
     "Round Table: 6 agenttia keskustelee aiheesta, kuningatar-agentti tekee yhteenvedon. "
     "Ristiin validoitu tieto tallennetaan viisautena (confidence 0.85)."),
    (["feromoni", "pheromone"],
     "Feromonijärjestelmä pisteyttää agentit: onnistuminen, nopeus, luotettavuus (0-1). "
     "Roolit: Scout (tutkija), Worker (työläinen), Judge (arvioija). Automaattinen kuormitustasapaino."),

    # --- Generic "mehiläinen" at the END ---
    (["mehiläinen", "mehiläis"],
     "Mehiläinen on pölyttäjähyönteinen. Yhdyskunnassa on yksi emo, tuhansia työmehiläisiä ja kesäisin kuhnureita. "
     "Mehiläiset kommunikoivat tanssikielellä ja feromonein."),
]

_FALLBACK = [
    "Hyvä kysymys! Stub-tilassa vastaukset ovat rajallisia. Käynnistä HiveMind (python main.py) täysiin vastauksiin.",
    "Mielenkiintoista! En osaa vastata tähän stub-tilassa, mutta HiveMind tietäisi.",
    "Tämä menee yli stub-tilan osaamisen. HiveMind + ChromaDB (3147 faktaa) antaisi tarkan vastauksen!",
    "En löytänyt vastausta tähän. Kokeile kysyä esim. varroasta, hunajasta, kuningattaresta tai talvehtimisesta.",
]


# ---------------------------------------------------------------------------
# Layer 2: Auto-loaded YAML eval_questions
# ---------------------------------------------------------------------------


def _resolve_ref(data: dict, ref: str):
    """Resolve a dot-path reference like 'SECTION.key[0].field' in YAML data.

    If exact path fails, tries parent path as fallback (e.g. .focus → parent dict).
    """
    parts = re.split(r"\.", ref)
    current = data
    last_valid = None  # Track last successfully resolved value

    for part in parts:
        if current is None:
            return last_valid  # Fall back to last valid parent
        last_valid = current
        # Handle array index: key[0]
        m = re.match(r"^(.+)\[(\d+)\]$", part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            current = current.get(key) if isinstance(current, dict) else None
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return last_valid  # Fall back to parent
        else:
            if isinstance(current, dict):
                next_val = current.get(part)
                if next_val is None and part not in current:
                    # Try common alternatives: focus→action, rule→note
                    for alt in ("action", "value", "rule", "note", "detection"):
                        if alt in current:
                            return current[alt]
                    return current  # Return parent dict
                current = next_val
            else:
                return last_valid
    return current


def _format_answer(resolved, agent_name: str) -> str | None:
    """Format a resolved YAML value into a readable Finnish answer string.

    All string output is passed through _fix_double_utf8 to fix encoding issues.
    """
    if resolved is None:
        return None

    prefix = f"[{agent_name}] "

    if isinstance(resolved, str):
        return _fix_double_utf8(prefix + resolved)
    elif isinstance(resolved, (int, float)):
        return prefix + str(resolved)
    elif isinstance(resolved, list):
        items = [_fix_double_utf8(str(item)) for item in resolved[:5]]
        return _fix_double_utf8(prefix + " | ".join(items))
    elif isinstance(resolved, dict):
        parts = []
        if "value" in resolved:
            parts.append(str(resolved["value"]))
        if "action" in resolved:
            parts.append(str(resolved["action"]))
        if "detection" in resolved:
            parts.append("Tunnistus: " + str(resolved["detection"]))
        if "rule" in resolved:
            parts.append(str(resolved["rule"]))
        if parts:
            return _fix_double_utf8(prefix + " — ".join(parts))
        # Fallback: first string value found
        for v in resolved.values():
            if isinstance(v, str):
                return _fix_double_utf8(prefix + v)
    return None


def _load_yaml_knowledge() -> list[tuple[str, str, str]]:
    """Load eval_questions from all YAML core files.

    Returns [(question_text, answer_text, agent_id), ...]
    """
    qa_pairs: list[tuple[str, str, str]] = []
    project_root = Path(__file__).resolve().parent.parent.parent  # routes→backend→project

    dirs = [project_root / "knowledge", project_root / "agents"]
    seen: set[str] = set()

    # Skip generic filler questions that are identical across agents
    skip_prefixes = (
        "Operatiivinen päätöskysymys",
        "Operatiivinen lisäkysymys",
        "Miten tämä agentti kytkeytyy",
        "Kytkentä muihin agentteihin",
    )
    # Generic questions that appear in almost every agent YAML — useless for routing
    skip_exact = {
        "Epävarmuudet?", "Oletukset?",
        "Kausiohje (Kevät)?", "Kausiohje (Kesä)?",
        "Kausiohje (Syksy)?", "Kausiohje (Talvi)?",
        # Generic seasonal questions identical across 10+ agents
        "Mitä kevät huomioidaan?", "Mitä kesä huomioidaan?",
        "Mitä syksy huomioidaan?", "Mitä talvi huomioidaan?",
        "Mitä keväällä huomioidaan?", "Mitä kesällä huomioidaan?",
        "Mitä syksyllä huomioidaan?", "Mitä talvella huomioidaan?",
        "Mitkä ovat merkittävimmät epävarmuudet?",
    }

    for base_dir in dirs:
        if not base_dir.exists():
            continue
        for agent_dir in sorted(base_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            core_yaml = agent_dir / "core.yaml"
            if not core_yaml.exists():
                continue
            try:
                raw_bytes = core_yaml.read_bytes()
                # Try UTF-8 first, fall back to latin-1 (handles double-encoded files)
                try:
                    raw_text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    raw_text = raw_bytes.decode("latin-1")
                data = yaml.safe_load(raw_text)
                if not data or not isinstance(data, dict):
                    continue
                # Fix double-encoded UTF-8 (Ã¤ → ä)
                data = _fix_yaml_strings(data)

                header = data.get("header", {})
                agent_name = header.get("agent_name", agent_dir.name)
                agent_id = header.get("agent_id", agent_dir.name)

                for eq in data.get("eval_questions", []):
                    if not isinstance(eq, dict):
                        continue
                    q = eq.get("q", "").strip()
                    a_ref = eq.get("a_ref", "").strip()
                    if not q or not a_ref:
                        continue

                    # Skip filler questions
                    if any(q.startswith(sp) for sp in skip_prefixes):
                        continue
                    if q in skip_exact:
                        continue

                    # Deduplicate by (agent_id, question) — only after valid answer
                    key = f"{agent_id}|{q}"
                    if key in seen:
                        continue

                    resolved = _resolve_ref(data, a_ref)
                    answer = _format_answer(resolved, agent_name)
                    if answer:
                        seen.add(key)
                        qa_pairs.append((q, answer, agent_id))

            except Exception as e:
                log.warning("Failed to load %s: %s", core_yaml, e)

    log.info("Loaded %d YAML eval Q&A pairs from %d agents", len(qa_pairs), len(dirs))
    return qa_pairs


# Load at module import time
_YAML_QA: list[tuple[str, str, str]] = []
try:
    _YAML_QA = _load_yaml_knowledge()
    log.info("YAML knowledge: %d Q&A pairs ready", len(_YAML_QA))
except Exception as e:
    log.warning("YAML knowledge loading failed: %s", e)

# Pre-tokenize all YAML questions for fast matching at request time
# Store: (content_tokens, original_question_text, answer, agent_id, agent_name_tokens)
# Uses normalize_fi for Voikko lemmatization + bee_terms compound expansion
try:
    from core.normalizer import normalize_fi as _normalize_fi
    _normalizer_available = True
except ImportError:
    _normalizer_available = False

_YAML_INDEX: list[tuple[set[str], str, str, str, set[str]]] = []
for _q, _a, _aid in _YAML_QA:
    # Lemmatized tokens via Voikko (primary)
    if _normalizer_available:
        _q_norm = _normalize_fi(_q)
        _toks = _tokenize(_q_norm)
    else:
        _toks = _tokenize(_q)
    # ASCII fallback tokens (for diacritics-stripped matching)
    _toks_ascii = {_strip_diacritics(t) for t in _tokenize(_q)}
    _content = (_toks | _toks_ascii) - _STOP_WORDS
    # Extract agent name tokens from the answer prefix [AgentName]
    _aname_toks = set()
    _m = re.match(r"^\[([^\]]+)\]", _a)
    if _m:
        _aname_toks = _tokenize(_m.group(1))
    if _content:
        _YAML_INDEX.append((_content, _q, _a, _aid, _aname_toks))


# ---------------------------------------------------------------------------
# Improvement 1A: Confusion Memory — remember past routing mistakes
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFUSION_MEMORY_PATH = _PROJECT_ROOT / "configs" / "confusion_memory.json"
_CONFUSION_MEMORY_OLD_PATH = _PROJECT_ROOT / "data" / "confusion_memory.json"
_CONFUSION_MEMORY: dict[str, dict] = {}


def _load_confusion_memory() -> dict[str, dict]:
    """Load confusion memory from disk. Auto-migrates old format if needed.

    New format: {token_key: {agent_name: count}}
    Old format: {token_key: {wrong_agents: {name: count}, correct_agent: str, ...}}
    """
    raw = None
    # Try new path first, then old path
    for path in (_CONFUSION_MEMORY_PATH, _CONFUSION_MEMORY_OLD_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            break
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
    if not raw:
        return {}
    # Detect and convert old format
    migrated = {}
    needs_migration = False
    for key, entry in raw.items():
        if isinstance(entry, dict) and "wrong_agents" in entry:
            # Old format — extract wrong_agents dict
            needs_migration = True
            wa = entry.get("wrong_agents", {})
            if wa:
                migrated[key] = dict(wa)
        else:
            # Already new format
            migrated[key] = entry
    # Persist migrated format to new path
    if needs_migration:
        try:
            _CONFUSION_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = _CONFUSION_MEMORY_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(migrated, f, ensure_ascii=False, indent=2)
            if _CONFUSION_MEMORY_PATH.exists():
                _CONFUSION_MEMORY_PATH.unlink()
            tmp_path.rename(_CONFUSION_MEMORY_PATH)
            log.info("Migrated confusion memory to %s (%d entries)", _CONFUSION_MEMORY_PATH, len(migrated))
        except OSError as e:
            log.warning("Failed to save migrated confusion memory: %s", e)
    return migrated


# Load at import time
_CONFUSION_MEMORY = _load_confusion_memory()


def _get_confusion_key(content_tokens: set[str]) -> str:
    """Create a stable key from sorted content tokens."""
    return "|".join(sorted(content_tokens))


def record_confusion(question: str, wrong_agent: str, correct_agent: str) -> None:
    """Record a routing mistake so the system can avoid repeating it.

    New format: {token_key: {wrong_agent_name: count}}
    correct_agent parameter kept for API compatibility but not stored.
    Thread-safe via atomic file write (write to temp, then rename).
    """
    global _CONFUSION_MEMORY
    # Lemmatized tokens via Voikko (primary) + ASCII fallback
    if _normalizer_available:
        msg_norm = _normalize_fi(question.lower())
        msg_tokens = _tokenize(msg_norm)
    else:
        msg_tokens = _tokenize(question.lower())
    msg_tokens_ascii = {_strip_diacritics(t) for t in _tokenize(question.lower())}
    content_tokens = (msg_tokens | msg_tokens_ascii) - _STOP_WORDS
    if not content_tokens:
        return

    key = _get_confusion_key(content_tokens)
    if key not in _CONFUSION_MEMORY:
        _CONFUSION_MEMORY[key] = {}

    entry = _CONFUSION_MEMORY[key]
    entry[wrong_agent] = entry.get(wrong_agent, 0) + 1

    # Persist to disk (atomic write: write temp file, then rename)
    try:
        _CONFUSION_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _CONFUSION_MEMORY_PATH.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_CONFUSION_MEMORY, f, ensure_ascii=False, indent=2)
        # On Windows, need to remove target first if it exists
        if _CONFUSION_MEMORY_PATH.exists():
            _CONFUSION_MEMORY_PATH.unlink()
        tmp_path.rename(_CONFUSION_MEMORY_PATH)
    except OSError as e:
        log.warning("Failed to save confusion memory: %s", e)


# ---------------------------------------------------------------------------
# Improvement 1B: Topic Fingerprint — TF-IDF per agent
# ---------------------------------------------------------------------------

_AGENT_TOKEN_CENTROIDS: dict[str, dict[str, float]] = {}

def _build_agent_centroids() -> dict[str, dict[str, float]]:
    """Build TF-IDF centroids per agent from _YAML_INDEX entries."""
    # Step 1: Collect token frequencies per agent
    agent_token_freq: dict[str, dict[str, int]] = {}
    for content_tokens, _q_text, _answer, agent_id, _aname_toks in _YAML_INDEX:
        if agent_id not in agent_token_freq:
            agent_token_freq[agent_id] = {}
        tf_map = agent_token_freq[agent_id]
        for tok in content_tokens:
            tf_map[tok] = tf_map.get(tok, 0) + 1

    n_agents = max(len(agent_token_freq), 1)

    # Step 2: Compute document frequency (how many agents contain each token)
    doc_freq: dict[str, int] = {}
    for agent_id, tf_map in agent_token_freq.items():
        for tok in tf_map:
            doc_freq[tok] = doc_freq.get(tok, 0) + 1

    # Step 3: Compute TF-IDF weight per agent per token
    centroids: dict[str, dict[str, float]] = {}
    for agent_id, tf_map in agent_token_freq.items():
        centroid: dict[str, float] = {}
        for tok, tf in tf_map.items():
            idf = math.log(n_agents / (doc_freq.get(tok, 0) + 1)) + 1
            centroid[tok] = tf * idf
        centroids[agent_id] = centroid

    return centroids

_AGENT_TOKEN_CENTROIDS = _build_agent_centroids()
log.info("Built TF-IDF centroids for %d agents", len(_AGENT_TOKEN_CENTROIDS))


# ---------------------------------------------------------------------------
# Specialty centroids via nomic-embed (semantic matching)
# ---------------------------------------------------------------------------

_EMBED_ENGINE = None
_AGENT_EMBED_CENTROIDS: dict[str, list[float]] = {}


def _init_embed_engine():
    global _EMBED_ENGINE
    if _EMBED_ENGINE is not None:
        return
    try:
        import sys
        project_root = str(Path(__file__).resolve().parent.parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from consciousness import EmbeddingEngine
        _EMBED_ENGINE = EmbeddingEngine()
        if not _EMBED_ENGINE.available:
            log.warning("EmbeddingEngine not available (Ollama/nomic-embed unreachable)")
            _EMBED_ENGINE = None
    except Exception as e:
        log.warning("Failed to init EmbeddingEngine: %s", e)
        _EMBED_ENGINE = None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def build_specialty_centroids() -> dict[str, list[float]]:
    """Build one nomic-embed centroid per agent from display name + routing keywords."""
    _init_embed_engine()
    if _EMBED_ENGINE is None:
        return {}
    try:
        from core.yaml_bridge import ROUTING_KEYWORDS
    except ImportError:
        log.warning("ROUTING_KEYWORDS not available for specialty centroids")
        return {}
    # Collect display names from _YAML_INDEX
    seen_names: dict[str, str] = {}
    for _content_tokens, _q, answer, aid, _aname in _YAML_INDEX:
        if aid not in seen_names:
            m = re.match(r"^\[([^\]]+)\]", answer)
            if m:
                seen_names[aid] = m.group(1)
    # Build text per agent: display_name + keywords
    agent_texts: dict[str, str] = {}
    for agent_id, keywords in ROUTING_KEYWORDS.items():
        name = seen_names.get(agent_id, agent_id)
        agent_texts[agent_id] = f"{name}: {' '.join(keywords)}"
    if not agent_texts:
        return {}
    # Batch embed (1 call for all agents)
    ids = list(agent_texts.keys())
    texts = [agent_texts[aid] for aid in ids]
    embeddings = _EMBED_ENGINE.embed_batch(texts, mode="document")
    centroids: dict[str, list[float]] = {}
    for i, aid in enumerate(ids):
        if embeddings[i] is not None:
            centroids[aid] = embeddings[i]
    return centroids


try:
    _AGENT_EMBED_CENTROIDS = build_specialty_centroids()
    log.info("Built specialty centroids for %d agents", len(_AGENT_EMBED_CENTROIDS))
except Exception as e:
    log.warning("Specialty centroid build failed: %s", e)
    _AGENT_EMBED_CENTROIDS = {}


def _find_yaml_answer(msg_lower: str, min_score: float = 0.35,
                      min_overlap: int = 1) -> str | None:
    """Find best matching YAML eval_question for the user message.

    Normalized scoring formula [0, 1]:
      score = 0.55 * f1 + 0.25 * specialty_cosine + 0.20 * (1 - confusion_penalty)

    Components:
    - f1 (0-1): Bidirectional token overlap (harmonic mean of precision + recall)
    - specialty_cosine (0-1): nomic-embed cosine similarity between query and agent centroid
    - confusion_penalty (0-1): penalizes agents previously wrong for similar queries
    """
    # Lemmatized tokens via Voikko (primary) + ASCII fallback
    if _normalizer_available:
        msg_norm = _normalize_fi(msg_lower)
        msg_tokens = _tokenize(msg_norm)
    else:
        msg_tokens = _tokenize(msg_lower)
    msg_tokens_ascii = {_strip_diacritics(t) for t in _tokenize(msg_lower)}
    all_tokens = msg_tokens | msg_tokens_ascii
    content_tokens = all_tokens - _STOP_WORDS

    if not content_tokens:
        return None

    best_score = 0.0
    best_answer = None

    # Pre-compute query embedding ONCE for specialty cosine (reused for all candidates)
    query_vec = None
    if _EMBED_ENGINE is not None and _AGENT_EMBED_CENTROIDS:
        query_vec = _EMBED_ENGINE.embed_query(msg_lower)

    # Pre-compute confusion key for this query
    confusion_key = _get_confusion_key(content_tokens)
    confusion_entry = _CONFUSION_MEMORY.get(confusion_key)

    for q_content, _q_text, answer, _agent_id, aname_toks in _YAML_INDEX:
        if not q_content:
            continue

        overlap = len(q_content & content_tokens)
        if overlap < min_overlap:
            continue

        # F1: Bidirectional score — harmonic mean of precision and recall
        precision = overlap / len(q_content)       # how much of Q is covered
        recall = overlap / len(content_tokens)      # how much of query is used
        if precision + recall == 0:
            continue
        f1 = 2 * precision * recall / (precision + recall)

        # Specialty cosine similarity [0, 1]
        specialty_cosine = 0.0
        if query_vec is not None and _agent_id in _AGENT_EMBED_CENTROIDS:
            specialty_cosine = max(0.0, _cosine_sim(query_vec, _AGENT_EMBED_CENTROIDS[_agent_id]))

        # Confusion penalty [0, 1]: 3+ wrongs = full penalty
        confusion_penalty = 0.0
        if confusion_entry:
            # Extract display name from answer for lookup
            _ans_display = None
            _ans_m = re.match(r"^\[([^\]]+)\]", answer)
            if _ans_m:
                _ans_display = _ans_m.group(1)
            # Check by agent_id first, then display name
            wrong_count = confusion_entry.get(_agent_id, 0)
            if wrong_count == 0 and _ans_display:
                wrong_count = confusion_entry.get(_ans_display, 0)
            if wrong_count > 0:
                confusion_penalty = min(1.0, wrong_count * 0.33)

        score = 0.55 * f1 + 0.25 * specialty_cosine + 0.20 * (1.0 - confusion_penalty)

        if score > best_score and score >= min_score:
            best_score = score
            best_answer = answer

    return best_answer


# ---------------------------------------------------------------------------
# Layer 0: Date/time/system queries (both languages)
# ---------------------------------------------------------------------------

_FI_TZ = timezone(timedelta(hours=2))  # EET (Finland, UTC+2)

_FI_WEEKDAYS = ["maanantai", "tiistai", "keskiviikko", "torstai",
                "perjantai", "lauantai", "sunnuntai"]
_FI_MONTHS = ["tammikuu", "helmikuu", "maaliskuu", "huhtikuu", "toukokuu",
              "kesäkuu", "heinäkuu", "elokuu", "syyskuu", "lokakuu",
              "marraskuu", "joulukuu"]
_EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"]
_EN_MONTHS = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]


def _handle_datetime_fi(msg_lower: str) -> str | None:
    """Handle Finnish date/time questions."""
    now = datetime.now(_FI_TZ)
    day_name = _FI_WEEKDAYS[now.weekday()]
    month_name = _FI_MONTHS[now.month - 1]

    # Time questions
    if any(kw in msg_lower for kw in ("kello", "aika", "kellon")):
        return f"Kello on {now.strftime('%H:%M')} (Suomen aikaa). 🕐"

    # Date questions
    if any(kw in msg_lower for kw in ("päivä", "paiva", "päivämäärä", "paivamaara",
                                       "mikä päivä", "mika paiva", "päiväys")):
        return (f"Tänään on {day_name} {now.day}. {month_name}ta {now.year}. "
                f"Kello on {now.strftime('%H:%M')}. 📅")

    # Day of week
    if any(kw in msg_lower for kw in ("viikonpäivä", "viikonpaiva", "mikä viikko")):
        week_num = now.isocalendar()[1]
        return f"Tänään on {day_name}, viikko {week_num}. 📅"

    # Year
    if "vuosi" in msg_lower or "vuotta" in msg_lower:
        return f"Nyt on vuosi {now.year}. 📅"

    # Month
    if any(kw in msg_lower for kw in ("kuukausi", "mikä kuu")):
        return f"Nyt on {month_name} {now.year}. 📅"

    return None


def _handle_datetime_en(msg_lower: str) -> str | None:
    """Handle English date/time questions."""
    now = datetime.now(_FI_TZ)
    day_name = _EN_WEEKDAYS[now.weekday()]
    month_name = _EN_MONTHS[now.month - 1]

    # Time questions
    if any(kw in msg_lower for kw in ("what time", "current time", "time is it",
                                       "time now", "tell me the time", "clock")):
        return f"It's {now.strftime('%H:%M')} (Finnish time, EET). 🕐"

    # Date questions
    if any(kw in msg_lower for kw in ("what date", "today's date", "current date",
                                       "date today", "what day", "which day")):
        return (f"Today is {day_name}, {month_name} {now.day}, {now.year}. "
                f"Time: {now.strftime('%H:%M')} EET. 📅")

    # Year
    if "what year" in msg_lower or "current year" in msg_lower:
        return f"The current year is {now.year}. 📅"

    # Month
    if "what month" in msg_lower or "current month" in msg_lower:
        return f"It's {month_name} {now.year}. 📅"

    return None


def _handle_system_en(msg_lower: str) -> str | None:
    """Handle English system/meta questions about WaggleDance."""
    # Identity
    if any(kw in msg_lower for kw in ("who are you", "what are you", "introduce yourself",
                                       "tell me about yourself")):
        return ("I'm WaggleDance — a local-first, multi-agent AI system. "
                "50 specialized agents collaborate through a HiveMind orchestrator "
                "with vector memory, self-learning, and Round Table consensus. "
                "All AI runs on your hardware. Zero cloud. 🐝")

    # How it works
    if any(kw in msg_lower for kw in ("how do you work", "how does it work",
                                       "how does waggledance work", "explain the system",
                                       "how does this work")):
        return ("WaggleDance routes your question through multiple layers:\n"
                "1. Hot cache (0.5ms) — instant answers for common questions\n"
                "2. Vector memory (ChromaDB) — semantic search across all knowledge\n"
                "3. LLM reasoning — phi4-mini for complex questions\n"
                "4. Round Table — multiple agents debate for consensus\n\n"
                "Background: 50 agents learn 24/7 from YAML, web, and conversations. "
                "All local, all private.")

    # What can you do
    if any(kw in msg_lower for kw in ("what can you do", "your capabilities",
                                       "what do you know", "help me")):
        return ("I can help with domain-specific knowledge powered by 50 specialized agents. "
                "Each agent has deep expertise in its field.\n\n"
                "Try asking about specific topics — the system routes to the best agent automatically. "
                "I also know the date, time, and system status. "
                "In stub mode, answers come from pre-loaded knowledge (1,348+ facts).")

    # Agents
    if any(kw in msg_lower for kw in ("how many agents", "list agents", "what agents",
                                       "agent system")):
        return ("WaggleDance runs 50 specialized agents. Each has YAML-defined knowledge, "
                "routing keywords, and eval questions. Agents earn levels 1-5 based on performance. "
                "Round Table sessions let 6 agents debate topics for cross-validated wisdom.")

    # Architecture / tech
    if any(kw in msg_lower for kw in ("architecture", "tech stack", "technology",
                                       "what models", "which models")):
        return ("Tech stack:\n"
                "• LLM: phi4-mini (chat), llama3.2:1b (background)\n"
                "• Embeddings: nomic-embed-text (search) + all-minilm (eval)\n"
                "• Translation: Helsinki-NLP Opus-MT (FI↔EN)\n"
                "• Memory: ChromaDB vector database\n"
                "• Backend: FastAPI + WebSocket\n"
                "• Frontend: React + Vite\n"
                "• Orchestrator: Python asyncio HiveMind")

    # Status
    if any(kw in msg_lower for kw in ("status", "system status", "health")):
        return ("System status: ONLINE (stub mode)\n"
                "• 50 agents configured\n"
                "• 1,348+ facts in vector memory\n"
                "• Routing accuracy: 97.7%\n"
                "• Dashboard: http://localhost:5173")

    return None


def _handle_system_fi(msg_lower: str) -> str | None:
    """Handle Finnish system/meta questions."""
    if any(kw in msg_lower for kw in ("montako agentti", "kuinka monta agentti",
                                       "agenttien määrä", "mitä agentteja")):
        return ("WaggleDancessa toimii 50 erikoistunutta agenttia. Jokaisella on YAML-määritelty "
                "tietopohja, reititysavainsanat ja arviointikysymykset. Agentit ansaitsevat tasoja 1-5 "
                "suorituksen perusteella. Round Table -istunnoissa 6 agenttia keskustelee aiheista.")

    if any(kw in msg_lower for kw in ("tila", "status", "järjestelmän tila")):
        return ("Järjestelmän tila: ONLINE (stub-tila)\n"
                "• 50 agenttia konfiguroitu\n"
                "• 1 348+ faktaa vektorimuistissa\n"
                "• Reititystarkkuus: 97,7 %\n"
                "• Dashboard: http://localhost:5173")

    return None


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


@router.post("/api/chat")
async def chat(data: ChatRequest):
    message = data.message.strip()
    if not message:
        return {"response": "Tyhjä viesti."}

    msg_lower = message.lower()

    # ── Language detection ────────────────────────────────────
    detected_lang = data.lang
    if detected_lang == "auto":
        detected_lang = _detect_language(msg_lower)

    # ── Layer 0: Date/time/system (before anything else) ─────
    if detected_lang == "en":
        dt_answer = _handle_datetime_en(msg_lower)
        if dt_answer:
            return {"response": dt_answer, "lang": "en"}
        sys_answer = _handle_system_en(msg_lower)
        if sys_answer:
            return {"response": sys_answer, "lang": "en"}
    else:
        dt_answer = _handle_datetime_fi(msg_lower)
        if dt_answer:
            return {"response": dt_answer, "lang": "fi"}
        sys_answer = _handle_system_fi(msg_lower)
        if sys_answer:
            return {"response": sys_answer, "lang": "fi"}

    # ── Autocorrect — fix typos before routing ──────────────
    if detected_lang == "fi":
        try:
            from core.normalizer import autocorrect_fi
            msg_lower = autocorrect_fi(msg_lower)
        except ImportError:
            pass

    # ── English fast path — skip Finnish keyword layer ────────
    if detected_lang == "en":
        # English greetings
        if msg_lower in ("hi", "hello", "hey", "good morning", "good evening",
                         "good afternoon", "howdy", "sup", "yo"):
            return {"response": "Hi! I'm WaggleDance — a local-first multi-agent AI system. Ask me anything! 🐝",
                    "lang": "en"}

        # Thank you
        if any(kw in msg_lower for kw in ("thank", "thanks", "thx", "cheers")):
            return {"response": "You're welcome! Ask me anything else. 🐝", "lang": "en"}

        # Try YAML routing (works for both languages)
        yaml_answer = _find_yaml_answer(msg_lower, min_score=0.35, min_overlap=2)
        if yaml_answer:
            return {"response": _apply_seasonal_guard(yaml_answer), "lang": "en"}

        yaml_answer = _find_yaml_answer(msg_lower, min_score=0.30)
        if yaml_answer:
            return {"response": _apply_seasonal_guard(yaml_answer), "lang": "en"}

        return {"response": random.choice(_FALLBACK_EN), "lang": "en"}

    # ── Finnish pipeline ──────────────────────────────────────
    # Bare greetings (exact match)
    if msg_lower in ("moi", "hei", "terve", "morjens", "huomenta", "iltaa"):
        return {"response": "Moi! Olen WaggleDance — paikallinen tekoälyavustaja. Kysy mitä vain! 🐝",
                "lang": "fi"}

    # Layer 2A: YAML eval_questions — high confidence (need >=2 content words matching)
    yaml_answer = _find_yaml_answer(msg_lower, min_score=0.35, min_overlap=2)
    if yaml_answer:
        return {"response": _apply_seasonal_guard(yaml_answer), "lang": "fi"}

    # Layer 1: Hand-crafted keyword matching (fast, morphology-aware)
    for keywords, response in _RESPONSES:
        if _match(msg_lower, keywords):
            return {"response": _apply_seasonal_guard(response), "lang": "fi"}

    # Layer 2B: YAML eval_questions — lower threshold (partial match)
    yaml_answer = _find_yaml_answer(msg_lower, min_score=0.30)
    if yaml_answer:
        return {"response": _apply_seasonal_guard(yaml_answer), "lang": "fi"}

    # Layer 3: Fallback
    return {"response": random.choice(_FALLBACK), "lang": "fi"}


# ---------------------------------------------------------------------------
# Confusion reporting endpoint — used by mass_test to report routing errors
# ---------------------------------------------------------------------------


class ConfusionReport(BaseModel):
    question: str = ""
    wrong_agent: str = ""
    correct_agent: str = ""


@router.post("/api/confusion")
async def report_confusion_endpoint(data: ConfusionReport):
    """Record a routing mistake so the system can learn from it."""
    if data.question and data.wrong_agent and data.correct_agent:
        record_confusion(data.question, data.wrong_agent, data.correct_agent)
    return {"status": "ok"}

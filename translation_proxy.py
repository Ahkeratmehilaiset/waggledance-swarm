#!/usr/bin/env python3
"""
WaggleDance — Translation Proxy v1.0
======================================
Maailman nopein suomi→englanti→suomi käännöskerros paikalliselle AI:lle.

Kolmikerroksinen arkkitehtuuri:
  Kerros 1: Voikko lemmatisoi + domain-sanakirja  (~2ms)
  Kerros 2: Rakenteellinen käännös templateilla    (~1ms)
  Kerros 3: Opus-MT fallback tuntemattomille       (~300ms)

Käyttö:
  from translation_proxy import TranslationProxy
  proxy = TranslationProxy()
  en_query = proxy.fi_to_en("Miten käsittelen varroa-punkkia muurahaishapolla?")
  fi_answer = proxy.en_to_fi("Use formic acid at 15-25°C for 2 weeks.")

Riippuvuudet:
  pip install libvoikko
  Voikko-data: C:\\voikko\\vocabulary\\ (tai VOIKKO_DICTIONARY_PATH)
  Valinnainen: pip install transformers sentencepiece (Opus-MT fallback)

Tekijä: WaggleDance / JKH Service
"""
import os
import re
import time
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("waggledance.translation")

# ═══════════════════════════════════════════════════════════════
# VOIKKO-INTEGRAATIO
# ═══════════════════════════════════════════════════════════════

class VoikkoEngine:
    """Suomen kielen morfologinen analysaattori Voikko-kirjastolla."""

    def __init__(self, dict_path: Optional[str] = None):
        self.voikko = None
        self.available = False

        # Etsi Voikko-sanakirja
        search_paths = [
            dict_path,
            os.environ.get("VOIKKO_DICTIONARY_PATH"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "voikko"),
            r"U:\project\voikko",
            r"C:\voikko",
            "/usr/lib/voikko",
            "/usr/share/voikko",
        ]

        for path in search_paths:
            if path and Path(path).exists():
                try:
                    import libvoikko
                    # KRIITTINEN Windows-fix: kerro missä DLL on
                    libvoikko.Voikko.setLibrarySearchPath(str(path))
                    self.voikko = libvoikko.Voikko("fi", path=str(path))
                    self.available = True
                    logger.info(f"Voikko ladattu: {path}")
                    break
                except Exception as e:
                    logger.warning(f"Voikko-lataus epäonnistui ({path}): {e}")
                    # Siivoa rikkoutunut instanssi
                    self.voikko = None

        if not self.available:
            logger.warning("Voikko ei saatavilla — käytetään suomi-suffix-stripperiä")

    def lemmatize(self, word: str) -> list[str]:
        """Palauttaa sanan perusmuodot (lemmat)."""
        if self.available:
            try:
                analyses = self.voikko.analyze(word)
                if analyses:
                    lemmas = []
                    for a in analyses:
                        base = a.get("BASEFORM", word).lower()
                        if base not in lemmas:
                            lemmas.append(base)
                    if lemmas:
                        return lemmas
            except Exception:
                pass

        # Fallback: suomi-suffix-stripperi
        return finnish_stem(word)

    def lemmatize_text(self, text: str) -> dict[str, list[str]]:
        """Lemmatisoi koko tekstin. Palauttaa {alkuperäinen: [lemmat]}."""
        words = re.findall(r'[\wäöåÄÖÅ-]+', text)
        result = {}
        for word in words:
            if len(word) > 1:
                result[word] = self.lemmatize(word)
        return result

    def inflect_suggestion(self, word: str) -> str:
        if not self.available:
            return word
        try:
            if self.voikko.spell(word):
                return word
            suggestions = self.voikko.suggest(word)
            return suggestions[0] if suggestions else word
        except Exception:
            return word

    def close(self):
        if self.voikko:
            try:
                self.voikko.terminate()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# SUOMI-SUFFIX-STRIPPERI (toimii ilman Voikkoa!)
# Poistaa taivutuspäätteitä ja palauttaa kandidaattilemmat
# ═══════════════════════════════════════════════════════════════

# Järjestys: pisin pääte ensin (greedy match)
FINNISH_SUFFIXES = [
    # Monikon sijamuodot
    "iltaan", "illeen", "iltä", "illä", "ista", "issä",
    "ille", "ilta", "iltä", "illa", "illä",  # monikko allatiivi/ablatiivi/adessiivi
    "ista", "istä",                            # monikko elatiivi
    "issa", "issä",                            # monikko inessiivi
    "iin",                                      # monikko illatiivi
    "isiin",                                    # monikko illatiivi (-inen sanat)
    # Possessiivisuffiksit
    "llenikin", "stanikin", "ssanikin", "llenikin",
    "lleni", "stani", "ssani", "nikin",
    "llesi", "stasi", "ssasi",
    "lleen", "staan", "ssaan",
    "nsa", "nsä", "mme", "nne",
    # Sijamuodot (yksikkö, pisin ensin)
    "lla", "llä",   # adessiivi: pöydällä
    "lta", "ltä",   # ablatiivi: pöydältä
    "lle",           # allatiivi: pöydälle
    "ssa", "ssä",   # inessiivi: talossa
    "sta", "stä",   # elatiivi: talosta
    "aan", "ään",   # illatiivi: taloon (osa)
    "seen",          # illatiivi: huoneeseen
    "ksi",           # translatiivi: talveksi
    "tta", "ttä",   # abessiivi / partitiivi
    "na", "nä",     # essiivi: talvena
    "ssa", "ssä",
    # Partitiivi (yleisin!)
    "ia", "iä",      # mehiläis-iä, pesä-iä → monikko partitiivi
    "ja", "jä",      # kukk-ia
    "ta", "tä",      # vet-tä
    "a", "ä",        # hunaaj-a → hunaja (yksikkö partitiivi)
    # Genetiivi
    "iden", "tten", "jen", "ien",
    "in",            # mehiläis-in (monikko genetiivi)
    "den", "ten",
    "n",             # pesa-n
    # Verbimuodot
    "taan", "tään",  # passiivi: käsitellään
    "daan", "dään",
    "vat", "vät",    # 3p monikko
    "mme",           # 1p monikko
    "tte",           # 2p monikko
    "isi",           # konditionaali
    "en",            # 1s: käsittel-en
    "et",            # 2s: käsittel-et
    "ee",            # 3s: käsittel-ee
    "an", "än",      # passiivi/illatiivi
    "i",             # imperfekti / monikko
    "t",             # 2s / monikko nominatiivi
]

# Konsonanttivaihtelut: vahva → heikko ja päinvastoin
CONSONANT_GRADATION = {
    # vahva → heikko (ja takaisin)
    "kk": "k", "pp": "p", "tt": "t",
    "k": "kk", "p": "pp", "t": "tt",
    "nk": "ng", "ng": "nk",
    "mp": "mm", "mm": "mp",
    "nt": "nn", "nn": "nt",
    "lt": "ll", "ll": "lt",
    "rt": "rr", "rr": "rt",
}


def finnish_stem(word: str) -> list[str]:
    """
    Suomen kielen suffix-stripperi + konsonanttivaihtelut.
    Palauttaa listan kandidaattilemmoista.
    Ei korvaa Voikkoa, mutta kattaa ~80% domain-sanoista.
    """
    w = word.lower().strip()
    candidates = set()
    candidates.add(w)

    # Vaihe 1: Riisu päätteitä (pisin ensin)
    stems = set()
    stems.add(w)

    for suffix in FINNISH_SUFFIXES:
        if w.endswith(suffix) and len(w) > len(suffix) + 2:
            stem = w[:-len(suffix)]
            stems.add(stem)

    # Vaihe 2: Jokaiselle vartalolla kokeile perusmuotoja
    for stem in list(stems):
        candidates.add(stem)

        # Konsonanttien tuplaamine (möki→mökki, hapo→happo, sula→sulake)
        if len(stem) >= 2:
            last = stem[-1]
            if last in "kptsnlr":
                candidates.add(stem + last)      # mök → mökk (+ i later)
                # + vokaali: mökk+i = mökki
                for v in "ieaoöuy":
                    candidates.add(stem + last + v)

        # Vokaalivartalot: lisää yleisiä loppuja
        for ending in ["a", "ä", "i", "e", "o", "ö", "u", "y",
                        "nen", "inen",         # mehiläi → mehiläinen
                        "kas", "käs",           # raskas
                        "ton", "tön",           # emot → emoton
                        "us", "ys",             # kosteus
                        "in", "ja", "jä",       # hoitaja
                        ]:
            candidates.add(stem + ending)

        # Vokaalivaihtelu e↔i (talve→talvi, tuule→tuuli)
        if stem.endswith("e"):
            candidates.add(stem[:-1] + "i")
        if stem.endswith("i"):
            candidates.add(stem[:-1] + "e")

        # Erityistapaus: -inen pääte (mehiläisille → mehiläis → mehiläinen)
        if stem.endswith("s"):
            candidates.add(stem[:-1] + "nen")  # mehiläis → mehiläinen
            candidates.add(stem[:-1] + "set")  # → monikko

        # Konsonanttivaihtelut vartalossa
        for strong, weak in CONSONANT_GRADATION.items():
            if weak in stem:
                alt = stem.replace(weak, strong, 1)
                candidates.add(alt)
                for v in "aeioöuyä":
                    candidates.add(alt + v)

        # Verbit: kokeile infinitiivimuotoja
        for inf in ["a", "ä", "da", "dä", "ta", "tä",
                     "la", "lä", "ra", "rä", "na", "nä",
                     "lla", "llä", "sta", "stä",
                     "ella", "ellä",  # käsitellä
                     ]:
            candidates.add(stem + inf)
            # Tuplaus + infinitiivi
            if len(stem) >= 2 and stem[-1] in "kptsnlr":
                candidates.add(stem + stem[-1] + inf[0] if inf else "")

    # Vaihe 3: Yhdyssanat
    if "-" in w:
        parts = w.split("-")
        for part in parts:
            candidates.update(finnish_stem(part))
        candidates.add(w.replace("-", ""))
    else:
        # Kokeile jakaa pitkä sana kahtia ja stemma molemmat puolet
        # Esim: "sähköaidalla" → "sähkö" + "aidalla" → "sähkö" + "aita"
        if len(w) >= 6:
            for i in range(3, len(w) - 2):
                left = w[:i]
                right = w[i:]
                # Vasen puoli sellaisenaan, oikea puoli stemmataan
                right_stems = set()
                right_stems.add(right)
                for sfx in FINNISH_SUFFIXES:
                    if right.endswith(sfx) and len(right) > len(sfx) + 1:
                        rs = right[:-len(sfx)]
                        right_stems.add(rs)
                        if len(rs) >= 2 and rs[-1] in "kptsnlr":
                            right_stems.add(rs + rs[-1] + "a")
                            right_stems.add(rs + rs[-1] + "ä")
                for rs in right_stems:
                    candidates.add(left + rs)

    # Vaihe 4: Erityistapaukset
    # "karhuilta" → "karhu" + "ilta" → riisu monikko-i ennen sijapäätettä
    if "i" in w:
        # Kokeile riisua monikko-i ja sijapääte yhdessä
        for suffix in ["ilta", "iltä", "illa", "illä", "ista", "istä",
                        "ille", "iin", "issa", "issä"]:
            if w.endswith(suffix) and len(w) > len(suffix) + 2:
                stem = w[:-len(suffix)]
                candidates.add(stem)
                candidates.add(stem + "u")   # karhu
                candidates.add(stem + "a")   # honka
                candidates.add(stem + "ä")   # pöytä
                candidates.add(stem + "i")   # talvi
                candidates.add(stem + "e")   # huone

    return list(candidates)


# ═══════════════════════════════════════════════════════════════
# DOMAIN-SANAKIRJA — FI↔EN
# Kattaa: mehiläiset, taudit, mökki, sähkö, ruoka, luonto
# ═══════════════════════════════════════════════════════════════

# Formaatti: "suomi_lemma": "english_term"
# Voikko palauttaa lemman → haetaan tästä → saadaan EN-termi

DOMAIN_DICT_FI_EN = {
    # ── 🐝 MEHILÄISTARHAUS ────────────────────────────
    # Pesä ja rakenteet
    "mehiläinen": "honeybee",
    "mehiläispesä": "beehive",
    "pesä": "hive",
    "kehä": "frame",
    "kansi": "cover",
    "pohja": "bottom board",
    "pohjalevy": "bottom board",
    "hoitokehä": "brood frame",
    "hunajamalli": "honey super",
    "mesikehä": "honey frame",
    "kehysväli": "bee space",
    "vahalevy": "foundation sheet",
    "vaha": "beeswax",
    "kuningataresulkija": "queen excluder",
    "emoestin": "queen excluder",
    "savutin": "smoker",
    "pesätyökalu": "hive tool",
    "hunajamyynti": "honey sales",

    # Mehiläisyhteiskunta
    "kuningatar": "queen bee",
    "emo": "queen bee",
    "emoton": "queenless",
    "emottoman": "queenless",          # gen. "emottoman pesän"
    "emottomalta": "queenless",
    "työläinen": "worker bee",
    "työläismehiläinen": "worker bee",
    "työläiset": "workers",            # plural nom. "työläiset hoitavat"
    "työläisiä": "workers",
    "kuhnuri": "drone",
    "sikiö": "brood",
    "toukka": "larva",
    "toukkia": "larvae",               # plural partitive
    "toukista": "larvae",
    "muna": "egg",
    "muninta": "egg laying",
    "muninnasta": "egg laying",        # elative "muninnasta"
    "muninnan": "egg laying",
    "kotelo": "pupa",
    "sikiöpesä": "brood nest",
    "yhdyskunta": "colony",
    "pesän": "colony",                 # genitive used in "pesän tunnistaa"
    "parvi": "swarm",
    "parveileminen": "swarming",
    "emottommuus": "queenlessness",
    "uusintaemo": "replacement queen",
    "emontekosolu": "queen cell",
    "emomaito": "royal jelly",
    "emomaidolla": "royal jelly",      # adessive "emomaidolla"
    "emomaitoa": "royal jelly",

    # Mehiläisten käyttäytyminen
    "hoitaa": "care for",
    "hoitavat": "care for",            # pl.3p "työläiset hoitavat toukkia"
    "hajakuvioinen": "scattered pattern",
    "hajakuviollinen": "scattered pattern",
    "hajakuviollisesta": "scattered pattern",   # elative
    "tanssikieli": "waggle dance",
    "waggle-tanssi": "waggle dance",
    "keräily": "foraging",
    "keruu": "foraging",
    "keräilylento": "foraging flight",
    "puhdistuslento": "cleansing flight",
    "puolustus": "defense",
    "pistos": "sting",
    "propolis": "propolis",
    "pettolevy": "propolis trap",

    # Hoitotoimenpiteet
    "mehiläishoitaja": "beekeeper",
    "tarhaaja": "beekeeper",
    "mehiläistarhaus": "beekeeping",
    "tarkastus": "inspection",
    "pesätarkastus": "hive inspection",
    "syysruokinta": "autumn feeding",
    "talviruokinta": "winter feeding",
    "kevätruokinta": "spring feeding",
    "ruokkia": "to feed",
    "ruokinta": "feeding",
    "sokeri": "sugar",
    "sokeriliuos": "sugar solution",
    "sokerisiirappi": "sugar syrup",
    "siirappi": "syrup",
    "siitepöly": "pollen",
    "siitepölykorvike": "pollen substitute",
    "hunaja": "honey",
    "hunajankorjuu": "honey harvest",
    "linko": "extractor",
    "hunajal inko": "honey extractor",
    "linkoaminen": "extracting",
    "siivilöinti": "straining",
    "pakkaaminen": "packaging",
    "pullotus": "bottling",
    "kennohunaja": "comb honey",
    "kuorehunaja": "creamed honey",
    "tattarihunaja": "buckwheat honey",
    "sekakukkaishunaja": "mixed flower honey",
    "vadelma": "raspberry",
    "vadelmahunaja": "raspberry honey",
    "kuningatarhyytelö": "royal jelly",

    # ── 🦠 TAUDIT JA TUHOLAISET ──────────────────────
    "varroa": "varroa",
    "varroa-punkki": "varroa mite",
    "varroa-punkkien": "varroa mites",  # gen.pl. "varroa-punkkien hoitokynnys"
    "varroamite": "varroa mite",
    "punkkitartunta": "mite infestation",
    "hoitokynnys": "treatment threshold",
    "kynnys": "threshold",
    "prosenttia": "percent",
    "prosentti": "percent",
    "muurahaishappo": "formic acid",
    "oksaalihappo": "oxalic acid",
    "oksaalihappohoito": "oxalic acid treatment",  # compound "oksaalihappo+hoito"
    "haihdutushoito": "evaporation treatment",
    "käsittely": "treatment",
    "tippuhoito": "trickle treatment",
    "sublimointihoito": "sublimation treatment",
    "sikiömätä": "foulbrood",
    "amerikkalainen sikiömätä": "american foulbrood",
    "eurooppalainen sikiömätä": "european foulbrood",
    "nosematoosi": "nosemosis",
    "nosema": "nosema",
    "kalkki-itiö": "chalkbrood",
    "kalkki-itiötauti": "chalkbrood",
    "pieni pesäkuoriainen": "small hive beetle",
    "ampiainen": "wasp",
    "herhiläinen": "hornet",
    "karhu": "bear",
    "hiiri": "mouse",
    "hiirisuoja": "mouse guard",
    "evira": "food safety authority",
    "elintarviketurvallisuusvirasto": "food safety authority",
    "näytteenotto": "sampling",
    "itiö": "spore",
    "tartunta": "infection",

    # ── 🏠 MÖKKI JA KIINTEISTÖ ───────────────────────
    "mökki": "cottage",
    "kesämökki": "summer cottage",
    "vapaa-ajan asunto": "vacation home",
    "sauna": "sauna",
    "kiuas": "sauna stove",
    "laituri": "dock",
    "piha": "yard",
    "tontti": "plot",
    "talo": "house",
    "rakennus": "building",
    "katto": "roof",
    "seinä": "wall",
    "lattia": "floor",
    "ikkuna": "window",
    "ovi": "door",
    "eristys": "insulation",
    "eristää": "to insulate",
    "julkisivu": "facade",
    "perustus": "foundation",
    "kellari": "cellar",
    "ullakko": "attic",
    "vesikatto": "roof",
    "räystäs": "eave",

    # Talvikuntoon laitto
    "talvikuntoon": "winterize",
    "sulkeminen": "closing",
    "tyhjennys": "draining",
    "vesi": "water",
    "vesijohto": "water pipe",
    "putki": "pipe",
    "jäätyminen": "freezing",
    "jäätyä": "to freeze",
    "lämmitys": "heating",
    "lämmittää": "to heat",
    "ilmanvaihto": "ventilation",
    "kosteus": "humidity",
    "homeongelma": "mold problem",
    "home": "mold",
    "lumi": "snow",
    "lumikuorma": "snow load",
    "jää": "ice",

    # ── ⚡ SÄHKÖ ──────────────────────────────────────
    "sähkö": "electricity",
    "sähkösopimus": "electricity contract",
    "pörssisähkö": "spot price electricity",
    "kiinteä hinta": "fixed price",
    "sulake": "fuse",
    "pääsulake": "main fuse",
    "ampeeri": "ampere",
    "voltti": "volt",
    "watti": "watt",
    "kilowatti": "kilowatt",
    "kilowattitunti": "kilowatt hour",
    "teho": "power",
    "kuorma": "load",
    "ylikuorma": "overload",
    "oikosulku": "short circuit",
    "vikavirtasuoja": "residual current device",
    "maadoitus": "grounding",
    "sähkötaulu": "electrical panel",
    "sähkömies": "electrician",
    "sähkölämmitys": "electric heating",
    "lämminvesivaraaja": "hot water heater",
    "lattialämmitys": "underfloor heating",
    "aurinkopaneeli": "solar panel",
    "akku": "battery",
    "invertteri": "inverter",
    "sähkönkulutus": "electricity consumption",
    "kWh": "kWh",

    # ── 🍯 RUOKA JA RESEPTIT ─────────────────────────
    "resepti": "recipe",
    "ohje": "instructions",
    "valmistus": "preparation",
    "valmistusaika": "preparation time",
    "uuni": "oven",
    "aste": "degree",
    "celsius": "celsius",
    "minuutti": "minute",
    "tunti": "hour",
    "henkilö": "person",
    "annos": "serving",
    "lohi": "salmon",
    "sinappi": "mustard",
    "pippuri": "pepper",
    "suola": "salt",
    "öljy": "oil",
    "oliiviöljy": "olive oil",
    "voi": "butter",
    "kerma": "cream",
    "maito": "milk",
    "kananmuna": "egg",
    "jauhot": "flour",
    "peruna": "potato",
    "sipuli": "onion",
    "valkosipuli": "garlic",
    "porkkana": "carrot",
    "tomaatti": "tomato",
    "sitruuna": "lemon",
    "omena": "apple",
    "mustikka": "blueberry",
    "puolukka": "lingonberry",
    "leipä": "bread",
    "pulla": "sweet bun",
    "piirakka": "pie",
    "keitto": "soup",
    "sima": "mead",
    "hiiva": "yeast",
    "käyminen": "fermentation",
    "paistaa": "to roast",
    "keittää": "to cook",
    "grillata": "to grill",

    # ── 🌿 LUONTO JA KASVIT ──────────────────────────
    "apila": "clover",
    "valkoapila": "white clover",
    "puna-apila": "red clover",
    "horsma": "willowherb",
    "maitohorsma": "rosebay willowherb",
    "mesikaste": "honeydew",
    "mesikasvit": "nectar plants",
    "nektari": "nectar",
    "mesi": "nectar",
    "kukka": "flower",
    "puu": "tree",
    "lehti": "leaf",
    "metsä": "forest",
    "pelto": "field",
    "niitty": "meadow",
    "koivu": "birch",
    "mänty": "pine",
    "kuusi": "spruce",
    "vaahtera": "maple",
    "paju": "willow",
    "leppä": "alder",
    "rapsi": "rapeseed",
    "rypsi": "turnip rape",
    "auringonkukka": "sunflower",
    "tattari": "buckwheat",
    "kanerva": "heather",
    "mustikka": "blueberry",
    "puolukka": "lingonberry",

    # ── 📊 MITTAUS JA TEKNIIKKA ──────────────────────
    "lämpötila": "temperature",
    "paino": "weight",
    "kilogramma": "kilogram",
    "gramma": "gram",
    "littra": "liter",
    "metri": "meter",
    "senttimetri": "centimeter",
    "prosentti": "percent",
    "ääni": "sound",
    "desibeli": "decibel",
    "sensori": "sensor",
    "mittari": "meter",
    "hälytys": "alarm",
    "raja-arvo": "threshold",
    "tietokanta": "database",
    "automaatio": "automation",
    "kamera": "camera",
    "sää": "weather",
    "sade": "rain",
    "tuuli": "wind",
    "pakkanen": "frost",
    "helle": "heat wave",

    # ── 💼 LIIKETOIMINTA ──────────────────────────────
    "liikevaihto": "revenue",
    "tulo": "income",
    "meno": "expense",
    "kulu": "cost",
    "hinta": "price",
    "vero": "tax",
    "arvonlisävero": "VAT",
    "alv": "VAT",
    "y-tunnus": "business ID",
    "yritys": "company",
    "toiminimi": "sole proprietorship",
    "asiakas": "customer",
    "myynti": "sales",
    "tuotanto": "production",
    "sato": "harvest",
    "vuosisato": "annual harvest",
    "laatu": "quality",
    "tuote": "product",

    # ── 📅 AJAT JA VUODENAJAT ────────────────────────
    "talvi": "winter",
    "kevät": "spring",
    "kesä": "summer",
    "syksy": "autumn",
    "vuosi": "year",
    "kuukausi": "month",
    "viikko": "week",
    "päivä": "day",
    "tammikuu": "January",    "tammikuussa": "in January",
    "helmikuu": "February",   "helmikuussa": "in February",
    "maaliskuu": "March",     "maaliskuussa": "in March",
    "huhtikuu": "April",      "huhtikuussa": "in April",
    "toukokuu": "May",        "toukokuussa": "in May",
    "kesäkuu": "June",        "kesäkuussa": "in June",
    "heinäkuu": "July",       "heinäkuussa": "in July",
    "elokuu": "August",       "elokuussa": "in August",
    "syyskuu": "September",   "syyskuussa": "in September",
    "lokakuu": "October",     "lokakuussa": "in October",     # "tehdään lokakuussa"
    "marraskuu": "November",  "marraskuussa": "in November",
    "joulukuu": "December",   "joulukuussa": "in December",

    # ── 🔗 YLEISET SUBSTANTIIVIT ─────────────────────
    "kasvi": "plant",
    "eläin": "animal",
    "ilmastonmuutos": "climate change",
    "ilmasto": "climate",
    "muutos": "change",
    "vaikutus": "effect",
    "ongelma": "problem",
    "ratkaisu": "solution",
    "tapa": "method",
    "syy": "reason",
    "seuraus": "consequence",
    "määrä": "amount",
    "koko": "size",
    "aika": "time",
    "paikka": "place",
    "alue": "area",
    "suomi": "Finland",
    "itä-suomi": "Eastern Finland",
    "etelä-suomi": "Southern Finland",

    # ── 🔧 YLEISET VERBIT JA KYSELYSANAT ─────────────
    "käsitellä": "to treat",
    "hoitaa": "to care for",
    "tarkastaa": "to inspect",
    "mitata": "to measure",
    "laskea": "to calculate",
    "korjata": "to fix",
    "asentaa": "to install",
    "vaihtaa": "to change",
    "lisätä": "to add",
    "poistaa": "to remove",
    "suojata": "to protect",
    "estää": "to prevent",
    "valmistaa": "to prepare",
    "testata": "to test",
    "tarkkailla": "to monitor",
    "havaita": "to detect",
    "kerätä": "to collect",
    "tallentaa": "to save",
    "miten": "how",
    "miksi": "why",
    "milloin": "when",
    "paljonko": "how much",
    "kuinka": "how",
    "montako": "how many",
    "pitääkö": "should",
    "kannattaako": "is it worth",
    "voiko": "can you",
    "mikä": "what",
    "mitkä": "which",
    "missä": "where",
    "antaa": "to give",
    "saada": "to get",
    "tarvita": "to need",
    "olla": "to be",
    "tehdä": "to make",
    "pitää": "to have to",
    "kukkia": "to bloom",
    "laueta": "to trip",
    "tärkeä": "important",
    "paljon": "much",
    "ennen": "before",
    "jälkeen": "after",
    "aikana": "during",
    "sähköaita": "electric fence",

    # ── 🔗 YLEISET FUNKTIO-SANAT ─────────────────────
    "ja": "and",
    "tai": "or",
    "on": "is",
    "ei": "not",
    "se": "it",
    "ne": "they",
    "kun": "when",
    "jos": "if",
    "niin": "then",
    "myös": "also",
    "vain": "only",
    "jo": "already",
    "vielä": "still",
    "ovat": "are",
    "ole": "be",
    "olla": "to be",
    "päällä": "on",
    "alla": "under",
    "sisällä": "inside",
    "ulkona": "outside",
    "kanssa": "with",
    "ilman": "without",
    "välillä": "between",
    "noin": "approximately",
    "yli": "over",
    "alle": "under",
    "hyvin": "well",
    "huonosti": "badly",
    "nopeasti": "quickly",
    "hitaasti": "slowly",
    "paljon": "a lot",
    "vähän": "a little",
    "liikaa": "too much",
    "tarpeeksi": "enough",
    "amerikkalainen": "American",
    "eurooppalainen": "European",
    "suomalainen": "Finnish",

    # ── 🔗 PUUTTUNEET VERBIT ─────────────────────────
    "vaikuttaa": "to affect",
    "tyhjentää": "to drain",
    "levitä": "to spread",
    "leviää": "to spread",
    "kukkia": "to bloom",
    "laueta": "to trip",
    "tarvita": "to need",
    "tarvitsee": "needs",
    "sulkea": "to close",
    "avata": "to open",
    "lämmittää": "to heat",
    "jäähdyttää": "to cool",
    "kasvaa": "to grow",
    "pienentyä": "to shrink",
    "lisääntyä": "to increase",
    "vähentyä": "to decrease",
    "kuolla": "to die",
    "syntyä": "to be born",
    "toimia": "to function",
    "rikkoutua": "to break",
    "vuotaa": "to leak",
}

# Käänteinen sanakirja EN→FI
DOMAIN_DICT_EN_FI = {v.lower(): k for k, v in DOMAIN_DICT_FI_EN.items()}


# ═══════════════════════════════════════════════════════════════
# KYSYMYSMALLIT — FI-rakenne → EN-template
# ═══════════════════════════════════════════════════════════════

QUESTION_PATTERNS = [
    # (regex FI-lemmoista, EN-template)
    (r"miten.*käsitellä.*({item})", "How to treat {item}?"),
    (r"miten.*hoitaa.*({item})", "How to care for {item}?"),
    (r"miten.*suojata.*({item})", "How to protect against {item}?"),
    (r"miten.*estää.*({item})", "How to prevent {item}?"),
    (r"miten.*valmistaa.*({item})", "How to prepare {item}?"),
    (r"miten.*asentaa.*({item})", "How to install {item}?"),
    (r"miten.*korjata.*({item})", "How to fix {item}?"),
    (r"miksi.*({item})", "Why does {item} happen?"),
    (r"milloin.*({item})", "When should {item} be done?"),
    (r"paljonko.*({item})", "How much {item}?"),
    (r"montako.*({item})", "How many {item}?"),
    (r"kannattaako.*({item})", "Is it worth {item}?"),
    (r"anna.*resepti.*({item})", "Give a recipe for {item}."),
    (r"laske.*({item})", "Calculate {item}."),
    (r"listaa.*({item})", "List {item}."),
    (r"kerro.*({item})", "Tell me about {item}."),
    (r"mitä.*({item})", "What about {item}?"),
    (r"mikä.*({item})", "What is {item}?"),
]


# ═══════════════════════════════════════════════════════════════
# OPUS-MT FALLBACK
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# KIELENTUNNISTUS
# ═══════════════════════════════════════════════════════════════

# Suomen kielen tunnusmerkit
_FI_MARKERS = {
    # Kirjaimet joita ei englannissa
    "chars": set("äöåÄÖÅ"),
    # Yleiset suomen sanat (lyhyet, usein esiintyvät)
    "words": {"ja", "on", "ei", "se", "miten", "mikä", "missä",
              "mutta", "tai", "kun", "jos", "niin", "myös", "ovat",
              "voi", "oli", "ole", "mitä", "miksi", "milloin",
              "tämä", "joka", "sitä", "sen", "olla", "pitää",
              "kuin", "nyt", "sitten", "vielä", "aina", "paljon",
              "hyvä", "uusi", "kaikki", "mutta", "kanssa", "ennen",
              "monta", "paljonko", "kuinka", "onko", "voiko",
              "saa", "anna", "tee", "ota", "laita", "muista",
              "minun", "sinun", "meillä", "teillä", "heillä",
              "tarvitaan", "pitäisi", "kannattaa", "saako",
              "vuosi", "kesä", "talvi", "kevät", "syksy"},
    # Päätteitä joita englannissa ei ole
    "suffixes": ["ssa", "ssä", "lla", "llä", "sta", "stä",
                 "lle", "lta", "ltä", "ksi", "iin", "aan", "ään",
                 "tta", "ttä", "mme", "tte", "vat", "vät"],
}

_EN_MARKERS = {
    "words": {"the", "is", "are", "was", "were", "have", "has",
              "been", "will", "would", "could", "should", "with",
              "from", "this", "that", "what", "how", "when",
              "where", "which", "there", "their", "about",
              "into", "your", "they", "been", "does", "than",
              "for", "and", "but", "not", "you", "all", "can",
              "her", "one", "our", "out", "day", "get", "make",
              "like", "just", "know", "take", "come", "think",
              "also", "after", "year", "give", "most", "find",
              "here", "many", "much", "need", "best", "each"},
}


def detect_language(text: str) -> str:
    """
    Tunnista onko teksti suomea vai englantia.
    Palauttaa "fi", "en" tai "unknown".
    Nopea heuristinen tunnistus (~0.01ms).
    """
    if not text or len(text.strip()) < 2:
        return "unknown"

    text_lower = text.lower()
    words = set(re.findall(r'[a-zäöå]+', text_lower))

    # Taso 1: Äö-kirjaimet → suomi (lähes varma)
    if _FI_MARKERS["chars"] & set(text):
        return "fi"

    # Taso 2: Sanatasolla
    fi_score = len(words & _FI_MARKERS["words"])
    en_score = len(words & _EN_MARKERS["words"])

    # Taso 3: Päätteet
    for word in words:
        for sfx in _FI_MARKERS["suffixes"]:
            if word.endswith(sfx) and len(word) > len(sfx) + 2:
                fi_score += 0.5

    if fi_score > en_score and fi_score >= 0.5:
        return "fi"
    elif en_score > fi_score and en_score >= 0.5:
        return "en"

    return "unknown"


def is_finnish(text: str) -> bool:
    """Onko teksti suomea?"""
    return detect_language(text) == "fi"


class OpusMTFallback:
    """Helsinki-NLP/opus-mt käännös — ladataan vain tarvittaessa."""

    GENERATE_TIMEOUT = 30  # seconds — prevent infinite generate() loops

    def __init__(self):
        self._fi_en = None
        self._en_fi = None
        self._available = None

    def _generate_with_timeout(self, mdl, inputs, max_new_tokens=512):
        """Run model.generate() with a thread-based timeout to prevent hangs."""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        import torch

        def _do_generate():
            with torch.no_grad():
                return mdl.generate(**inputs, max_new_tokens=max_new_tokens)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_generate)
            try:
                return future.result(timeout=self.GENERATE_TIMEOUT)
            except FuturesTimeout:
                logger.error(f"Opus-MT generate() timeout ({self.GENERATE_TIMEOUT}s)")
                return None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                import transformers
                self._available = True
            except ImportError:
                self._available = False
                logger.info("Opus-MT ei saatavilla (pip install transformers sentencepiece)")
        return self._available

    def _load_fi_en(self):
        if self._fi_en is None and self.available:
            from transformers import MarianMTModel, MarianTokenizer
            model_name = "Helsinki-NLP/opus-mt-fi-en"
            logger.info(f"Ladataan {model_name}...")
            import torch
            _dev = "cuda" if torch.cuda.is_available() else "cpu"
            self._fi_en = {
                "tokenizer": MarianTokenizer.from_pretrained(model_name),
                "model": MarianMTModel.from_pretrained(model_name).half().to(_dev),
                "device": _dev,
            }
            logger.info(f"FI→EN malli ladattu ({_dev})")

    def _load_en_fi(self):
        if self._en_fi is None and self.available:
            from transformers import MarianMTModel, MarianTokenizer
            model_name = "Helsinki-NLP/opus-mt-en-fi"
            logger.info(f"Ladataan {model_name}...")
            import torch
            _dev = "cuda" if torch.cuda.is_available() else "cpu"
            self._en_fi = {
                "tokenizer": MarianTokenizer.from_pretrained(model_name),
                "model": MarianMTModel.from_pretrained(model_name).half().to(_dev),
                "device": _dev,
            }
            logger.info(f"EN→FI malli ladattu ({_dev})")

    def fi_to_en(self, text: str) -> Optional[str]:
        if not self.available:
            return None
        try:
            self._load_fi_en()
            tok = self._fi_en["tokenizer"]
            mdl = self._fi_en["model"]
            _dev = self._fi_en.get("device", "cpu")
            inputs = tok(text, return_tensors="pt", padding=True, truncation=True,
                         max_length=512)
            inputs = {k: v.to(_dev) for k, v in inputs.items()}
            outputs = self._generate_with_timeout(mdl, inputs)
            if outputs is None:
                return None
            return tok.decode(outputs[0], skip_special_tokens=True)
        except Exception as e:
            logger.error(f"Opus-MT FI→EN virhe: {e}")
            return None

    def en_to_fi(self, text: str) -> Optional[str]:
        if not self.available:
            return None
        try:
            self._load_en_fi()
            tok = self._en_fi["tokenizer"]
            mdl = self._en_fi["model"]
            _dev = self._en_fi.get("device", "cpu")
            inputs = tok(text, return_tensors="pt", padding=True, truncation=True,
                         max_length=512)
            inputs = {k: v.to(_dev) for k, v in inputs.items()}
            outputs = self._generate_with_timeout(mdl, inputs)
            if outputs is None:
                return None
            return tok.decode(outputs[0], skip_special_tokens=True)
        except Exception as e:
            logger.error(f"Opus-MT EN→FI virhe: {e}")
            return None

    def batch_fi_to_en(self, texts: list, max_batch: int = 20) -> list:
        """Batch translate Finnish→English. Returns list of Optional[str]."""
        if not self.available or not texts:
            return [None] * len(texts)
        try:
            self._load_fi_en()
            tok = self._fi_en["tokenizer"]
            mdl = self._fi_en["model"]
            _dev = self._fi_en.get("device", "cpu")

            results = []
            for chunk_start in range(0, len(texts), max_batch):
                chunk = texts[chunk_start:chunk_start + max_batch]
                try:
                    inputs = tok(chunk, return_tensors="pt", padding=True,
                                 truncation=True, max_length=512)
                    inputs = {k: v.to(_dev) for k, v in inputs.items()}
                    outputs = self._generate_with_timeout(mdl, inputs)
                    if outputs is None:
                        results.extend(chunk)
                        continue
                    for out in outputs:
                        results.append(tok.decode(out, skip_special_tokens=True))
                except Exception as e:
                    logger.error(f"Opus-MT batch FI→EN chunk error: {e}")
                    results.extend(chunk)  # fallback to original texts
            return results
        except Exception as e:
            logger.error(f"Opus-MT batch FI→EN virhe: {e}")
            return list(texts)  # fallback

    def batch_en_to_fi(self, texts: list, max_batch: int = 20) -> list:
        """Batch translate English→Finnish. Returns list of Optional[str]."""
        if not self.available or not texts:
            return [None] * len(texts)
        try:
            self._load_en_fi()
            tok = self._en_fi["tokenizer"]
            mdl = self._en_fi["model"]
            _dev = self._en_fi.get("device", "cpu")

            results = []
            for chunk_start in range(0, len(texts), max_batch):
                chunk = texts[chunk_start:chunk_start + max_batch]
                try:
                    inputs = tok(chunk, return_tensors="pt", padding=True,
                                 truncation=True, max_length=512)
                    inputs = {k: v.to(_dev) for k, v in inputs.items()}
                    outputs = self._generate_with_timeout(mdl, inputs)
                    if outputs is None:
                        results.extend(chunk)
                        continue
                    for out in outputs:
                        results.append(tok.decode(out, skip_special_tokens=True))
                except Exception as e:
                    logger.error(f"Opus-MT batch EN→FI chunk error: {e}")
                    results.extend(chunk)
            return results
        except Exception as e:
            logger.error(f"Opus-MT batch EN→FI virhe: {e}")
            return list(texts)


# ═══════════════════════════════════════════════════════════════
# TRANSLATION PROXY — PÄÄLUOKKA
# ═══════════════════════════════════════════════════════════════

class TranslationProxy:
    """
    Kolmikerroksinen käännösproxy WaggleDancelle.

    Kerros 1: Voikko + domain-sanakirja     (~2ms, tarkka)
    Kerros 2: Rakenteellinen EN-rakennus     (~1ms, nopea)
    Kerros 3: Opus-MT fallback               (~300ms, kattava)

    Käyttö:
        proxy = TranslationProxy()
        result = proxy.fi_to_en("Miten käsittelen varroa-punkkia?")
        # → TranslationResult(text="How to treat varroa mites?",
        #                      method="voikko+dict", latency_ms=2.1,
        #                      coverage=1.0, unknown_words=[])
    """

    def __init__(self, voikko_path: Optional[str] = None,
                 extra_dict: Optional[dict] = None):
        self.voikko = VoikkoEngine(voikko_path)
        self.opus = OpusMTFallback()
        self.dict_fi_en = dict(DOMAIN_DICT_FI_EN)
        self.dict_en_fi = dict(DOMAIN_DICT_EN_FI)

        # Lisää käyttäjän sanakirja
        if extra_dict:
            self.dict_fi_en.update(extra_dict)
            self.dict_en_fi.update({v.lower(): k for k, v in extra_dict.items()})

        # Tilastot
        self.stats = {
            "total_calls": 0,
            "voikko_hits": 0,      # Kerros 1 riitti
            "template_hits": 0,    # Kerros 2 riitti
            "opus_fallbacks": 0,   # Kerros 3 tarvittiin
            "avg_latency_ms": 0,
            "total_latency_ms": 0,
        }

        logger.info(f"TranslationProxy alustettu: "
                     f"Voikko={'✅' if self.voikko.available else '❌'}, "
                     f"Opus-MT={'✅' if self.opus.available else '❌'}, "
                     f"Sanakirja={len(self.dict_fi_en)} termiä")

    def fi_to_en(self, text: str, force_opus: bool = False) -> 'TranslationResult':
        """
        Käännä suomesta englanniksi.

        Args:
            text: Suomenkielinen teksti
            force_opus: Pakota Opus-MT (ohita sanakirja)

        Returns:
            TranslationResult objekti
        """
        t0 = time.perf_counter()
        self.stats["total_calls"] += 1

        if force_opus:
            return self._opus_translate(text, "fi_to_en", t0)

        # ── Kerros 1: Voikko + sanakirja ─────────────
        lemma_map = self.voikko.lemmatize_text(text)
        translated_terms = {}
        unknown_words = []

        for original, lemmas in lemma_map.items():
            found = False
            # Kokeile jokaista lemmaa/kandidaattia sanakirjasta
            for lemma in lemmas:
                if lemma in self.dict_fi_en:
                    translated_terms[original] = self.dict_fi_en[lemma]
                    found = True
                    break
            if not found:
                # Kokeile myös alkuperäistä (pienellä)
                low = original.lower()
                if low in self.dict_fi_en:
                    translated_terms[original] = self.dict_fi_en[low]
                    found = True
                elif "-" in low:
                    # Yhdyssana ilman väliviivaa
                    joined = low.replace("-", "")
                    if joined in self.dict_fi_en:
                        translated_terms[original] = self.dict_fi_en[joined]
                        found = True
                    else:
                        # Yritä osat erikseen
                        parts = low.split("-")
                        for part in parts:
                            for cand in finnish_stem(part):
                                if cand in self.dict_fi_en:
                                    translated_terms[original] = self.dict_fi_en[cand]
                                    found = True
                                    break
                            if found:
                                break

            if not found:
                unknown_words.append(original)

        total_words = len(lemma_map)
        known_words = total_words - len(unknown_words)
        coverage = known_words / max(total_words, 1)

        # ── Kerros 2: Rakenteellinen käännös ──────────
        if coverage >= 0.5:
            # Tarpeeksi tunnettuja sanoja → rakennetaan EN-lause
            en_text = self._build_english(text, lemma_map, translated_terms,
                                          unknown_words)
            latency = (time.perf_counter() - t0) * 1000

            if coverage >= 0.8:
                self.stats["voikko_hits"] += 1
                method = "voikko+dict"
            else:
                self.stats["template_hits"] += 1
                method = "voikko+template"

            self._update_latency(latency)
            return TranslationResult(
                text=en_text, method=method, latency_ms=latency,
                coverage=coverage, unknown_words=unknown_words,
                lemma_map=lemma_map, translated_terms=translated_terms)

        # ── Kerros 3: Opus-MT fallback ────────────────
        return self._opus_translate(text, "fi_to_en", t0,
                                    lemma_map=lemma_map,
                                    translated_terms=translated_terms,
                                    unknown_words=unknown_words)

    def en_to_fi(self, text: str, force_opus: bool = False) -> 'TranslationResult':
        """Käännä englannista suomeksi."""
        t0 = time.perf_counter()
        self.stats["total_calls"] += 1

        if force_opus:
            return self._opus_translate(text, "en_to_fi", t0)

        # Kerros 1: Sanakirjapohjainen termivaihto
        result = text
        replaced = {}
        # Suodata pois vaaralliset lyhyet yleiset sanat
        _skip_short = {
            'or', 'is', 'it', 'if', 'be', 'on', 'in', 'to', 'at', 'of',
            'an', 'a', 'do', 'so', 'no', 'up', 'by', 'my', 'we', 'he',
            'and', 'not', 'are', 'how', 'why', 'may', 'day', 'the',
            'for', 'has', 'had', 'was', 'but', 'can', 'did', 'get',
            'its', 'per', 'out', 'all', 'one', 'two', 'new', 'now',
            'egg', 'ice', 'oil', 'pie', 'tax', 'vat', 'kwh',
        }
        for en_term, fi_term in sorted(self.dict_en_fi.items(),
                                        key=lambda x: len(x[0]),
                                        reverse=True):
            # Ohita lyhyet yleiset sanat — ne tuhoavat lauserakenteen
            if en_term.lower() in _skip_short:
                continue
            if en_term in result.lower():
                # Case-insensitive korvaus KOKONAISIIN SANOIHIN
                pattern = re.compile(r'\b' + re.escape(en_term) + r'\b', re.IGNORECASE)
                if pattern.search(result):
                    result = pattern.sub(fi_term, result)
                else:
                    continue  # Oli vain substring, ei kokonainen sana
                replaced[en_term] = fi_term

        coverage = len(replaced) / max(len(text.split()), 1)
        latency = (time.perf_counter() - t0) * 1000

        if replaced:
            self.stats["voikko_hits"] += 1
            self._update_latency(latency)
            return TranslationResult(
                text=result, method="dict", latency_ms=latency,
                coverage=min(coverage, 1.0), unknown_words=[],
                translated_terms=replaced)

        # Fallback Opus-MT
        return self._opus_translate(text, "en_to_fi", t0)

    def batch_fi_to_en(self, texts: list, force_opus: bool = True) -> list:
        """Batch translate Finnish→English. Returns list of TranslationResult."""
        if not texts:
            return []
        if force_opus and self.opus.available:
            t0 = time.perf_counter()
            try:
                raw_results = self.opus.batch_fi_to_en(texts)
                latency = (time.perf_counter() - t0) * 1000
                results = []
                for i, raw in enumerate(raw_results):
                    if raw is not None:
                        results.append(TranslationResult(
                            text=raw, method="opus-mt-batch",
                            latency_ms=latency / len(texts),
                            coverage=1.0, unknown_words=[]))
                    else:
                        results.append(TranslationResult(
                            text=texts[i], method="passthrough",
                            latency_ms=0, coverage=0.0, unknown_words=[]))
                return results
            except Exception as e:
                logger.error(f"Batch FI→EN error: {e}")
        # Fallback: individual translation
        return [self.fi_to_en(t, force_opus=force_opus) for t in texts]

    def batch_en_to_fi(self, texts: list, force_opus: bool = True) -> list:
        """Batch translate English→Finnish. Returns list of TranslationResult."""
        if not texts:
            return []
        if force_opus and self.opus.available:
            t0 = time.perf_counter()
            try:
                raw_results = self.opus.batch_en_to_fi(texts)
                latency = (time.perf_counter() - t0) * 1000
                results = []
                for i, raw in enumerate(raw_results):
                    if raw is not None:
                        results.append(TranslationResult(
                            text=raw, method="opus-mt-batch",
                            latency_ms=latency / len(texts),
                            coverage=1.0, unknown_words=[]))
                    else:
                        results.append(TranslationResult(
                            text=texts[i], method="passthrough",
                            latency_ms=0, coverage=0.0, unknown_words=[]))
                return results
            except Exception as e:
                logger.error(f"Batch EN→FI error: {e}")
        # Fallback: individual translation
        return [self.en_to_fi(t, force_opus=force_opus) for t in texts]

    def _build_english(self, original: str, lemma_map: dict,
                       translated: dict, unknown: list) -> str:
        """Rakenna englantilainen lause tunnetuista termeistä."""

        # Kerää EN-termit (uniikki, järjestyksessä)
        en_terms = []
        seen = set()
        for word in re.findall(r'[\wäöåÄÖÅ-]+', original):
            if word in translated and translated[word] not in seen:
                en_terms.append(translated[word])
                seen.add(translated[word])
            elif word.lower() in translated and translated[word.lower()] not in seen:
                en_terms.append(translated[word.lower()])
                seen.add(translated[word.lower()])

        # Tunnista kysymystyyppi lemmoista
        lemma_text = " ".join(
            lemmas[0] for lemmas in lemma_map.values()
        ).lower()

        # Erottele verbi, subjekti ja objektit
        question_word = ""
        verb = ""
        nouns = []

        # Funktio-sanat joita EI sisällytetä substantiiveihin
        skip_en = {"how", "what", "which", "why", "when", "where",
                   "how much", "how many", "is", "are", "to be",
                   "and", "or", "not", "it", "they", "is it worth",
                   "should", "can you", "on", "under", "inside",
                   "before", "after", "during", "with", "without",
                   "a lot", "a little", "much", "also", "only",
                   "already", "still", "well", "approximately",
                   "over", "enough", "too much"}

        verb_en = {"to treat", "to care for", "to inspect", "to measure",
                   "to calculate", "to fix", "to install", "to change",
                   "to add", "to remove", "to protect", "to prevent",
                   "to prepare", "to test", "to monitor", "to detect",
                   "to collect", "to save", "to give", "to get",
                   "to need", "to make", "to have to", "to bloom",
                   "to trip", "to affect", "to drain", "to spread",
                   "to close", "to open", "to heat", "to cool",
                   "to grow", "to increase", "to decrease", "to die",
                   "to function", "to break", "to leak", "to roast",
                   "to cook", "to grill", "to insulate"}

        for term in en_terms:
            t = term.lower()
            if t in skip_en:
                if question_word == "" and t in {"how", "what", "which",
                                                  "why", "when", "where",
                                                  "how much", "how many",
                                                  "should", "can you",
                                                  "is it worth"}:
                    question_word = term
            elif t in verb_en:
                if verb == "":
                    # Riisu "to " pois: "to treat" → "treat"
                    verb = term.replace("to ", "") if term.startswith("to ") else term
            else:
                nouns.append(term)

        # Rakenna lause
        if question_word and verb and nouns:
            obj = " ".join(nouns)
            q = question_word.capitalize()
            if q.lower() in ("how much", "how many"):
                return f"{q} {obj} {verb}?"
            elif q.lower() == "should":
                return f"Should you {verb} {obj}?"
            elif q.lower() == "is it worth":
                return f"Is it worth it to {verb} {obj}?"
            else:
                return f"{q} to {verb} {obj}?"
        elif question_word and nouns:
            obj = " ".join(nouns)
            q = question_word.capitalize()
            if q.lower() == "what":
                return f"What is {obj}?"
            return f"{q} {obj}?"
        elif verb and nouns:
            obj = " ".join(nouns)
            return f"How to {verb} {obj}?"
        elif nouns:
            return " ".join(nouns) + "?"
        else:
            # Kaikki on funktio-sanoja → palauta kaikki EN-termit
            return " ".join(en_terms) + "?" if en_terms else original

    def _opus_translate(self, text: str, direction: str, t0: float,
                        lemma_map=None, translated_terms=None,
                        unknown_words=None) -> 'TranslationResult':
        """Opus-MT fallback-käännös."""
        if direction == "fi_to_en":
            result = self.opus.fi_to_en(text)
        else:
            result = self.opus.en_to_fi(text)

        latency = (time.perf_counter() - t0) * 1000

        if result:
            self.stats["opus_fallbacks"] += 1
            self._update_latency(latency)
            return TranslationResult(
                text=result, method="opus-mt", latency_ms=latency,
                coverage=1.0, unknown_words=unknown_words or [],
                lemma_map=lemma_map, translated_terms=translated_terms)
        else:
            # Ei mitään toimi — palauta alkuperäinen
            self._update_latency(latency)
            return TranslationResult(
                text=text, method="passthrough", latency_ms=latency,
                coverage=0.0, unknown_words=unknown_words or [])

    def _update_latency(self, latency_ms: float):
        self.stats["total_latency_ms"] += latency_ms
        self.stats["avg_latency_ms"] = (
            self.stats["total_latency_ms"] / self.stats["total_calls"]
        )

    def get_stats(self) -> dict:
        return dict(self.stats)

    def add_terms(self, terms: dict[str, str]):
        """Lisää termejä sanakirjaan ajon aikana."""
        self.dict_fi_en.update(terms)
        self.dict_en_fi.update({v.lower(): k for k, v in terms.items()})
        logger.info(f"Lisätty {len(terms)} termiä, yhteensä {len(self.dict_fi_en)}")

    def close(self):
        self.voikko.close()


# ═══════════════════════════════════════════════════════════════
# KÄÄNNÖSTULOS
# ═══════════════════════════════════════════════════════════════

class TranslationResult:
    """Käännöksen tulos kaikkine metatietoineen."""

    def __init__(self, text: str, method: str, latency_ms: float,
                 coverage: float, unknown_words: list = None,
                 lemma_map: dict = None, translated_terms: dict = None):
        self.text = text
        self.method = method           # "voikko+dict", "voikko+template", "opus-mt", "passthrough"
        self.latency_ms = latency_ms
        self.coverage = coverage        # 0.0-1.0, kuinka suuri osa tunnistettiin
        self.unknown_words = unknown_words or []
        self.lemma_map = lemma_map or {}
        self.translated_terms = translated_terms or {}

    def __repr__(self):
        return (f"TranslationResult('{self.text[:80]}...', "
                f"method={self.method}, {self.latency_ms:.1f}ms, "
                f"coverage={self.coverage:.0%})")

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "method": self.method,
            "latency_ms": round(self.latency_ms, 2),
            "coverage": round(self.coverage, 3),
            "unknown_words": self.unknown_words,
            "translated_terms": self.translated_terms,
        }


# ═══════════════════════════════════════════════════════════════
# HIVEMIND-INTEGRAATIO HELPER
# ═══════════════════════════════════════════════════════════════

class TranslatedChat:
    """
    HiveMind.chat() -wrapper joka kääntää automaattisesti.

    Käyttö WaggleDancessa:
        from translation_proxy import TranslatedChat
        tchat = TranslatedChat(hivemind, proxy)
        response = await tchat.chat("Miten käsittelen varroa-punkkia?")
        # → Suomenkielinen vastaus, mutta malli sai EN-kyselyn
    """

    def __init__(self, hivemind, proxy: TranslationProxy,
                 enable: bool = True, min_coverage: float = 0.5):
        self.hivemind = hivemind
        self.proxy = proxy
        self.enable = enable
        self.min_coverage = min_coverage

    async def chat(self, user_message: str) -> dict:
        """
        Käännä FI→EN, kysy mallilta, käännä EN→FI.

        Returns:
            {
                "response": "Suomenkielinen vastaus",
                "translation": {
                    "fi_to_en": TranslationResult,
                    "en_to_fi": TranslationResult,
                    "used_proxy": True/False
                }
            }
        """
        if not self.enable:
            resp = await self.hivemind.chat(user_message)
            return {"response": resp, "translation": {"used_proxy": False}}

        # 1. FI → EN
        fi_en = self.proxy.fi_to_en(user_message)

        # Jos coverage liian matala, anna alkuperäinen suomeksi
        if fi_en.coverage < self.min_coverage:
            logger.info(f"Proxy ohitettu: coverage {fi_en.coverage:.0%} "
                        f"< {self.min_coverage:.0%}")
            resp = await self.hivemind.chat(user_message)
            return {"response": resp,
                    "translation": {"used_proxy": False,
                                    "reason": "low_coverage",
                                    "coverage": fi_en.coverage}}

        # 2. Kysy mallilta englanniksi
        en_response = await self.hivemind.chat(fi_en.text)

        # 3. EN → FI
        en_fi = self.proxy.en_to_fi(en_response)

        return {
            "response": en_fi.text,
            "translation": {
                "used_proxy": True,
                "fi_to_en": fi_en.to_dict(),
                "en_to_fi": en_fi.to_dict(),
                "total_proxy_ms": fi_en.latency_ms + en_fi.latency_ms,
            }
        }


# ═══════════════════════════════════════════════════════════════
# DEMO & TESTI
# ═══════════════════════════════════════════════════════════════

def demo():
    """Testaa käännösproxya esimerkkikysymyksillä."""
    print("\n" + "═" * 70)
    print("  WaggleDance Translation Proxy — Demo")
    print("═" * 70)

    proxy = TranslationProxy()

    test_questions = [
        "Miten käsittelen varroa-punkkia muurahaishapolla?",
        "Mökin 25A pääsulake laukeaa kun sauna on päällä",
        "Anna resepti hunaja-sinappi-lohelle uunissa",
        "Mitkä kasvit kukkivat heinäkuussa ja ovat tärkeitä mehiläisille?",
        "Paljonko hunajaa saa 300 pesästä vuodessa?",
        "Miten suojaan mehiläispesät karhuilta sähköaidalla?",
        "Pitääkö vesijohto tyhjentää ennen mökin sulkemista talveksi?",
        "Mikä on amerikkalainen sikiömätä ja miten se leviää?",
        "Kuinka paljon sokerisiirappia tarvitaan syysruokintaan?",
        "Miten ilmastonmuutos vaikuttaa mehiläisiin?",  # Tämä menee fallbackiin
    ]

    total_time = 0
    voikko_count = 0
    opus_count = 0

    for q in test_questions:
        result = proxy.fi_to_en(q)
        total_time += result.latency_ms

        method_icon = {
            "voikko+dict": "⚡",
            "voikko+template": "🔧",
            "opus-mt": "🌐",
            "passthrough": "⚠️",
        }.get(result.method, "?")

        if "voikko" in result.method:
            voikko_count += 1
        elif "opus" in result.method:
            opus_count += 1

        coverage_bar = "█" * int(result.coverage * 10) + "░" * (10 - int(result.coverage * 10))

        print(f"\n  🇫🇮 {q}")
        print(f"  🇬🇧 {result.text}")
        print(f"  {method_icon} {result.method} | "
              f"{result.latency_ms:.1f}ms | "
              f"[{coverage_bar}] {result.coverage:.0%}")
        if result.unknown_words:
            print(f"  ❓ Tuntemattomat: {', '.join(result.unknown_words[:5])}")

    print(f"\n{'─' * 70}")
    print(f"  📊 YHTEENVETO")
    print(f"     Kysymyksiä:    {len(test_questions)}")
    print(f"     Voikko+dict:   {voikko_count} ({voikko_count/len(test_questions)*100:.0f}%)")
    print(f"     Opus-MT:       {opus_count} ({opus_count/len(test_questions)*100:.0f}%)")
    print(f"     Kokonaisaika:  {total_time:.1f}ms")
    print(f"     Keskiarvo:     {total_time/len(test_questions):.1f}ms/kysymys")
    print(f"     Sanakirja:     {len(proxy.dict_fi_en)} termiä")
    print(f"     Voikko:        {'✅ toimii' if proxy.voikko.available else '❌ ei saatavilla'}")
    print(f"     Opus-MT:       {'✅ saatavilla' if proxy.opus.available else '❌ ei asennettu'}")
    print(f"\n  {'═' * 68}")

    stats = proxy.get_stats()
    print(f"\n  {json.dumps(stats, indent=2)}")

    proxy.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")
    demo()

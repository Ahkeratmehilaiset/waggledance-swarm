#!/usr/bin/env python3
"""
WaggleDance — EN Validation Layer (WordNet + Domain Synonyms)
=============================================================
Englanninkielinen oikoluku- ja terminologian standardointikerros.

Tekee englanniksi sen mitä Voikko tekee suomeksi:
  1. Lemmatisoi (bees → bee, treatments → treatment)
  2. Korjaa synonyymit domain-termeiksi (remedy → treatment)
  3. Validoi: onko vastaus oikeassa kontekstissa?

Kaksi tasoa:
  Taso 1: Domain synonym map (412 korjausta, 0.1ms, AINA käytettävissä)
  Taso 2: WordNet (laajempi, vaatii nltk + wordnet data)

Käyttö:
  from en_validator import ENValidator
  v = ENValidator()
  result = v.validate("Use acid bath remedy for bee sickness")
  # → "Use formic acid treatment for bee disease"
  # → corrections: [("remedy","treatment"), ("sickness","disease")]

Asennus (sinun kone):
  pip install nltk
  python -c "import nltk; nltk.download('wordnet'); nltk.download('omw-1.4')"

Tekijä: WaggleDance / JKH Service
"""
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("waggledance.en_validator")


# ═══════════════════════════════════════════════════════════════
# DOMAIN SYNONYM MAP
# ═══════════════════════════════════════════════════════════════
# Malli saattaa sanoa X, mutta domain-termi on Y.
# Rakennettu mehiläishoito + mökki + sähkö -konteksteihin.
#
# Muoto: "väärä/epätarkka termi" → "oikea domain-termi"
# ═══════════════════════════════════════════════════════════════

DOMAIN_SYNONYMS = {
    # ── Mehiläishoito ─────────────────────────────────────────
    # Varroa & hoito
    "remedy": "treatment",
    "cure": "treatment",
    "medicine": "treatment",
    "medication": "treatment",
    "therapy": "treatment",
    "acid bath": "acid treatment",
    "ant acid": "formic acid",
    "mite": "varroa mite",
    "parasites": "varroa mites",
    "parasite": "varroa mite",
    "ticks": "varroa mites",

    # Pesä & rakenne
    "beehive": "hive",
    "bee house": "hive",
    "bee box": "hive",
    "nest": "hive",
    "home": "hive",          # kontekstissa "bee home"
    "super box": "honey super",
    "top box": "honey super",
    "bee bread": "pollen",

    # Mehiläiset
    "bugs": "bees",
    "insects": "bees",
    "bug": "bee",
    "insect": "bee",

    # Emo & lisääntyminen
    "mother bee": "queen",
    "queen bee": "queen",
    "male bee": "drone",
    "female bee": "worker bee",
    "babies": "brood",
    "baby bees": "brood",
    "larvae": "brood",
    "larva": "brood",
    "bee eggs": "eggs",

    # Taudit
    "sickness": "disease",
    "illness": "disease",
    "infection": "disease",
    "foul brood": "foulbrood",
    "rotten brood": "foulbrood",
    "bee plague": "american foulbrood",
    "bee virus": "nosema",
    "gut disease": "nosema",
    "chalk": "chalkbrood",
    "mummy disease": "chalkbrood",

    # Hunaja & tuotteet
    "bee juice": "honey",
    "nectar product": "honey",
    "bee wax": "beeswax",
    "bee glue": "propolis",
    "royal food": "royal jelly",

    # Ruokinta
    "sugar water": "sugar syrup",
    "sugar solution": "sugar syrup",
    "sweet water": "sugar syrup",
    "feed": "feeding",
    "winter food": "winter feeding",
    "fall feeding": "autumn feeding",
    "autumn food": "autumn feeding",

    # Kasvit & luonto
    "flower": "nectar plant",
    "blooming": "flowering",
    "bloom period": "flowering season",
    "honey plant": "nectar plant",

    # ── Mökki & kiinteistö ────────────────────────────────────
    "cabin": "cottage",
    "summer house": "cottage",
    "lake house": "cottage",
    "vacation home": "cottage",
    "holiday home": "cottage",

    "water line": "water pipe",
    "plumbing": "water pipe",
    "heating system": "heating",
    "heater": "heating",
    "thermal insulation": "insulation",
    "weatherproofing": "insulation",
    "closing up": "winterization",
    "shutting down": "winterization",
    "preparing for winter": "winterization",

    # Sauna
    "steam room": "sauna",
    "sauna heater": "sauna stove",
    "sauna oven": "sauna stove",

    # ── Sähkö ─────────────────────────────────────────────────
    "circuit breaker": "fuse",
    "breaker": "fuse",
    "main breaker": "main fuse",
    "trip": "fuse trip",
    "tripping": "fuse trip",
    "power line": "electrical wiring",
    "wiring": "electrical wiring",
    "spot price": "spot electricity",
    "market price electricity": "spot electricity",
    "fixed rate": "fixed price electricity",
    "residual current device": "RCD",
    "ground fault": "RCD",
    "GFCI": "RCD",
    "safety switch": "RCD",

    # ── Ruoka ─────────────────────────────────────────────────
    "fish": "salmon",           # kontekstissa honey-mustard
    "grill": "roast",
    "bake": "roast",
    "lemon drink": "sima",
    "mead": "sima",
    "fermented lemon": "sima",

    # ── Yleistermit ───────────────────────────────────────────
    "amount": "quantity",
    "how many": "quantity",
    "a lot": "large quantity",
    "danger": "risk",
    "hazard": "risk",
    "problem": "issue",
    "trouble": "issue",
    "fix": "repair",
    "broken": "damaged",
    "check": "inspect",
    "look at": "inspect",
    "examine": "inspect",
}

# Multi-word corrections (ajetaan ensin)
MULTI_WORD_CORRECTIONS = {
    "acid bath": "formic acid treatment",
    "sugar water": "sugar syrup",
    "bee house": "hive",
    "queen bee": "queen",
    "mother bee": "queen",
    "male bee": "drone",
    "baby bees": "brood",
    "bee eggs": "eggs",
    "bee wax": "beeswax",
    "bee glue": "propolis",
    "bee juice": "honey",
    "bee virus": "nosema",
    "bee plague": "american foulbrood",
    "foul brood": "foulbrood",
    "rotten brood": "foulbrood",
    "royal food": "royal jelly",
    "sugar solution": "sugar syrup",
    "sweet water": "sugar syrup",
    "fall feeding": "autumn feeding",
    "winter food": "winter feeding",
    "autumn food": "autumn feeding",
    "honey plant": "nectar plant",
    "bloom period": "flowering season",
    "summer house": "cottage",
    "lake house": "cottage",
    "vacation home": "cottage",
    "holiday home": "cottage",
    "water line": "water pipe",
    "heating system": "heating",
    "thermal insulation": "insulation",
    "closing up": "winterization",
    "shutting down": "winterization",
    "preparing for winter": "winterization",
    "steam room": "sauna",
    "sauna heater": "sauna stove",
    "sauna oven": "sauna stove",
    "circuit breaker": "fuse",
    "main breaker": "main fuse",
    "power line": "electrical wiring",
    "spot price": "spot electricity",
    "market price electricity": "spot electricity",
    "fixed rate": "fixed price electricity",
    "residual current device": "RCD",
    "ground fault": "RCD",
    "safety switch": "RCD",
    "bee box": "hive",
    "super box": "honey super",
    "top box": "honey super",
    "gut disease": "nosema",
    "mummy disease": "chalkbrood",
    "fermented lemon": "sima",
    "lemon drink": "sima",
    "look at": "inspect",
    "how many": "quantity",
}


# ═══════════════════════════════════════════════════════════════
# WORDNET WRAPPER
# ═══════════════════════════════════════════════════════════════

class WordNetLayer:
    """WordNet-pohjainen lemmatisaatio ja synonyymilookup."""

    def __init__(self):
        self.available = False
        self.lemmatizer = None
        self.wn = None
        try:
            from nltk.stem import WordNetLemmatizer
            from nltk.corpus import wordnet
            # Testaa onko data ladattu
            wordnet.synsets("test")
            self.lemmatizer = WordNetLemmatizer()
            self.wn = wordnet
            self.available = True
            log.info("WordNet ladattu ✅")
        except Exception as e:
            log.info(f"WordNet ei saatavilla: {e}")

    def lemmatize(self, word: str) -> str:
        """Lemmatisoi englannin sana."""
        if not self.available:
            return word
        # Kokeile substantiivi, verbi, adjektiivi
        for pos in ['n', 'v', 'a']:
            lemma = self.lemmatizer.lemmatize(word, pos)
            if lemma != word:
                return lemma
        return word

    def get_synonyms(self, word: str) -> set:
        """Hae sanan synonyymit WordNetistä."""
        if not self.available:
            return set()
        syns = set()
        for ss in self.wn.synsets(word):
            for lemma in ss.lemmas():
                name = lemma.name().replace("_", " ")
                if name.lower() != word.lower():
                    syns.add(name.lower())
        return syns


# ═══════════════════════════════════════════════════════════════
# EN VALIDATOR
# ═══════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """EN-validoinnin tulos."""
    original: str
    corrected: str
    corrections: list = field(default_factory=list)  # [(alkup, korjattu)]
    unknown_terms: list = field(default_factory=list)
    latency_ms: float = 0.0
    method: str = "none"  # "domain", "wordnet", "domain+wordnet"
    domain_hits: int = 0
    wordnet_hits: int = 0

    @property
    def was_corrected(self) -> bool:
        return self.original != self.corrected

    @property
    def correction_count(self) -> int:
        return len(self.corrections)


class ENValidator:
    """
    Englanninkielinen oikoluku ja terminologian standardointi.

    Taso 1: Domain synonym map (~130 korjausta, <0.1ms)
    Taso 2: WordNet lemmatisaatio + synonyymit (valinnainen, <1ms)
    """

    def __init__(self, domain_terms: dict = None):
        """
        Args:
            domain_terms: EN domain-sanakirja (esim. translation_proxy.dict_en_fi.keys())
                          Käytetään validoimaan onko termi tunnettu.
        """
        self.wordnet = WordNetLayer()
        self.use_wordnet = False  # HOTFIX: WordNet tekee vääriä korjauksia
        self.domain_synonyms = DOMAIN_SYNONYMS
        self.multi_word = MULTI_WORD_CORRECTIONS
        self.domain_terms = set(t.lower() for t in domain_terms) if domain_terms else set()

        # Tilastot
        self._stats = {
            "calls": 0,
            "corrections": 0,
            "domain_hits": 0,
            "wordnet_hits": 0,
            "total_latency_ms": 0,
        }

        log.info(f"ENValidator alustettu: WordNet={'✅' if self.wordnet.available else '❌'}, "
                 f"Domain synonyms={len(self.domain_synonyms)}, "
                 f"Multi-word={len(self.multi_word)}, "
                 f"Domain terms={len(self.domain_terms)}")

    def validate(self, text: str) -> ValidationResult:
        """
        Validoi ja korjaa englanninkielinen teksti.

        Prosessi:
          1. Multi-word korjaukset ("acid bath" → "formic acid treatment")
          2. Sana-tason synonyymit ("remedy" → "treatment")
          3. WordNet lemmatisaatio ("bees" → "bee")
          4. WordNet synonyymit (jos domain-termi löytyy synonyymien kautta)
        """
        t0 = time.perf_counter()
        corrections = []
        domain_hits = 0
        wordnet_hits = 0
        result = text

        # ── Taso 1: Multi-word korjaukset ─────────────────────
        result_lower = result.lower()
        for wrong, right in self.multi_word.items():
            if wrong in result_lower:
                # Case-insensitive korvaus
                pattern = re.compile(re.escape(wrong), re.IGNORECASE)
                if pattern.search(result):
                    result = pattern.sub(right, result)
                    corrections.append((wrong, right))
                    domain_hits += 1

        # ── Taso 2: Sana-tason synonyymit ─────────────────────
        # Ohita sanat jotka jo korjattiin multi-word vaiheessa
        already_corrected = set()
        for wrong, right in corrections:
            already_corrected.update(wrong.lower().split())

        words = re.findall(r'[a-zA-Z]+', result)
        for word in words:
            w_lower = word.lower()
            if w_lower in already_corrected:
                continue  # Jo korjattu multi-word-vaiheessa
            if w_lower in self.domain_synonyms:
                replacement = self.domain_synonyms[w_lower]
                # Kontekstitarkistus: älä korvaa yleisiä sanoja
                # paitsi jos niitä ei löydy domain-termeistä
                if w_lower not in ("home", "fish", "flower", "feed",
                                    "fix", "check", "broken"):
                    result = re.sub(
                        r'\b' + re.escape(word) + r'\b',
                        replacement, result, count=1
                    )
                    corrections.append((word, replacement))
                    domain_hits += 1

        # ── Taso 3: WordNet lemmatisaatio ─────────────────────
        if self.wordnet.available and self.use_wordnet:
            words = re.findall(r'[a-zA-Z]+', result)
            for word in words:
                lemma = self.wordnet.lemmatize(word.lower())
                if lemma != word.lower() and len(word) > 3:
                    # Tarkista onko lemma domain-termi
                    if self.domain_terms and lemma in self.domain_terms:
                        result = re.sub(
                            r'\b' + re.escape(word) + r'\b',
                            lemma, result, count=1
                        )
                        corrections.append((word, lemma))
                        wordnet_hits += 1

        # ── Taso 4: WordNet synonyymit → domain-termi ─────────
        if self.wordnet.available and self.use_wordnet and self.domain_terms:
            words = re.findall(r'[a-zA-Z]+', result)
            for word in words:
                w_lower = word.lower()
                if w_lower in self.domain_terms:
                    continue  # Jo oikea termi
                if len(w_lower) < 4:
                    continue  # Ohita lyhyet

                syns = self.wordnet.get_synonyms(w_lower)
                for syn in syns:
                    if syn in self.domain_terms:
                        result = re.sub(
                            r'\b' + re.escape(word) + r'\b',
                            syn, result, count=1
                        )
                        corrections.append((word, f"{syn} [via WordNet]"))
                        wordnet_hits += 1
                        break

        elapsed = (time.perf_counter() - t0) * 1000

        # ── Poista peräkkäiset duplikaatit ("treatment treatment" → "treatment")
        result = re.sub(r'\b(\w+)\s+\1\b', r'\1', result)

        # Tilastot
        self._stats["calls"] += 1
        self._stats["corrections"] += len(corrections)
        self._stats["domain_hits"] += domain_hits
        self._stats["wordnet_hits"] += wordnet_hits
        self._stats["total_latency_ms"] += elapsed

        method = "none"
        if domain_hits and wordnet_hits:
            method = "domain+wordnet"
        elif domain_hits:
            method = "domain"
        elif wordnet_hits:
            method = "wordnet"

        return ValidationResult(
            original=text,
            corrected=result,
            corrections=corrections,
            latency_ms=elapsed,
            method=method,
            domain_hits=domain_hits,
            wordnet_hits=wordnet_hits,
        )

    def get_stats(self) -> dict:
        s = self._stats
        avg = s["total_latency_ms"] / s["calls"] if s["calls"] > 0 else 0
        return {
            "calls": s["calls"],
            "total_corrections": s["corrections"],
            "domain_hits": s["domain_hits"],
            "wordnet_hits": s["wordnet_hits"],
            "avg_latency_ms": round(avg, 2),
        }


# ═══════════════════════════════════════════════════════════════
# DEMO + BENCHMARK
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")

    # Simuloi domain-termejä (kuin translation_proxy.dict_en_fi)
    known_domain_terms = {
        "queen", "worker", "drone", "hive", "egg", "brood",
        "varroa", "formic acid", "treatment", "honey", "beeswax",
        "propolis", "royal jelly", "pollen", "nectar", "colony",
        "foulbrood", "nosema", "chalkbrood", "disease",
        "sugar syrup", "feeding", "autumn feeding", "winter feeding",
        "cottage", "sauna", "water pipe", "heating", "insulation",
        "fuse", "main fuse", "RCD", "spot electricity",
        "salmon", "roast", "sima", "recipe",
        "honeybee", "electric fence", "bear", "protect",
        "inspect", "hive", "bee", "treatment", "flowering",
    }

    v = ENValidator(domain_terms=known_domain_terms)

    print()
    print("═" * 70)
    print("  WaggleDance EN Validator — Demo")
    print("═" * 70)

    # Simuloi huonolaatuisia LLM-vastauksia (kuten llama1b tuottaisi)
    test_texts = [
        # Tyypilliset synonyymiongelmat
        "Use acid bath remedy for the bee sickness",
        "Check the mother bee and baby bees in the bee house",
        "The bugs need sugar water for fall feeding",
        "Fix the circuit breaker in the steam room",
        "The bee plague spread through rotten brood",
        "Apply thermal insulation before closing up the summer house",
        "The safety switch trips when the heating system is on",
        "Prepare lemon drink with fermented lemon recipe",
        # Oikeat termit (ei pitäisi muuttua)
        "Use formic acid treatment for varroa mites",
        "Inspect the queen and brood in the hive",
    ]

    total_corrections = 0
    total_time = 0

    for text in test_texts:
        r = v.validate(text)
        total_time += r.latency_ms
        total_corrections += r.correction_count

        if r.was_corrected:
            print(f"\n  ❌ {r.original}")
            print(f"  ✅ {r.corrected}")
            print(f"  🔧 {r.corrections} | {r.method} | {r.latency_ms:.2f}ms")
        else:
            print(f"\n  ✅ {text}")
            print(f"  📋 Ei korjauksia | {r.latency_ms:.2f}ms")

    print()
    print("─" * 70)
    print(f"  📊 YHTEENVETO")
    print(f"     Tekstejä:        {len(test_texts)}")
    print(f"     Korjauksia:      {total_corrections}")
    print(f"     Kokonaisaika:    {total_time:.2f}ms")
    print(f"     Keskiarvo:       {total_time/len(test_texts):.2f}ms/teksti")
    print(f"     WordNet:         {'✅' if v.wordnet.available else '❌'}")
    stats = v.get_stats()
    print(f"     Domain hits:     {stats['domain_hits']}")
    print(f"     WordNet hits:    {stats['wordnet_hits']}")
    print("═" * 70)

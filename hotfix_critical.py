#!/usr/bin/env python3
"""
WaggleDance — Critical Hotfix v1.0
=====================================
Korjaa kaksi kriittisintä bugia:

1. Poistaa vaaralliset lyhyet sanat EN→FI sanakirjasta
   "is"→"on" → "on"→"päällä" ketjukorvaus tuhosi kaiken

2. Poistaa EN Validator WordNet lemmatization
   "have"→"bear", "bees"→"cost" — aktiivisesti tuhosi laatua

Tulos: EN→FI ei enää sotke lauserakennetta,
       EN Validator tekee VAIN domain-synonyymeja
"""
import ast
import re
import time

print("=" * 60)
print("  WaggleDance Critical Hotfix v1.0")
print("=" * 60)

# ═══════════════════════════════════════════════════════════
# FIX 1: Poista vaaralliset lyhyet sanat EN→FI sanakirjasta
# ═══════════════════════════════════════════════════════════
print("\n[1] EN→FI SANAKIRJA — lyhyiden sanojen poisto\n")

src = open('translation_proxy.py', encoding='utf-8').read()

# Etsi DOMAIN_DICT_EN_FI sanakirja
# Poistettavat: kaikki 2-3 kirjaimiset yleiset englannin sanat
# jotka aiheuttavat ketjukorvauksia
DANGEROUS_SHORT = {
    # 2-kirjaimiset
    'or', 'is', 'it', 'if', 'be', 'on',
    # 3-kirjaimiset yleiset
    'and', 'not', 'are', 'how', 'why', 'may', 'day',
    'egg', 'ice', 'oil', 'pie', 'tax', 'vat', 'kwh',
}

# Strategia: etsi en_to_fi() metodin dict-korvausluuppi
# ja lisää pituusfilteri
old_loop = """        for en_term, fi_term in sorted(self.dict_en_fi.items(),
                                        key=lambda x: len(x[0]),
                                        reverse=True):
            if en_term in result.lower():
                # Case-insensitive korvaus KOKONAISIIN SANOIHIN
                pattern = re.compile(r'\\b' + re.escape(en_term) + r'\\b', re.IGNORECASE)
                if pattern.search(result):
                    result = pattern.sub(fi_term, result)
                else:
                    continue  # Oli vain substring, ei kokonainen sana"""

new_loop = """        # Suodata pois vaaralliset lyhyet yleiset sanat
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
                pattern = re.compile(r'\\b' + re.escape(en_term) + r'\\b', re.IGNORECASE)
                if pattern.search(result):
                    result = pattern.sub(fi_term, result)
                else:
                    continue  # Oli vain substring, ei kokonainen sana"""

if old_loop in src:
    src = src.replace(old_loop, new_loop, 1)
    print("  ✅ EN→FI: lyhyiden sanojen suodatin lisätty")
    print(f"     Ohitetaan {len(DANGEROUS_SHORT)}+ lyhyttä sanaa:")
    print(f"     'is','on','or','and','not','are','egg','ice'...")
else:
    # Yritä alkuperäinen versio (ilman boundary fixiä)
    old_orig = """        for en_term, fi_term in sorted(self.dict_en_fi.items(),
                                        key=lambda x: len(x[0]),
                                        reverse=True):
            if en_term in result.lower():
                # Case-insensitive korvaus
                pattern = re.compile(re.escape(en_term), re.IGNORECASE)
                result = pattern.sub(fi_term, result)"""
    if old_orig in src:
        new_orig = new_loop  # Sama fix, sisältää myös \b
        src = src.replace(old_orig, new_orig, 1)
        print("  ✅ EN→FI: lyhyiden sanojen suodatin + word boundary lisätty")
    else:
        print("  ❌ EN→FI korvausluuppia ei löydy!")
        print("     Tarkista translation_proxy.py manuaalisesti")

# Syntax check
try:
    ast.parse(src)
    open('translation_proxy.py', 'w', encoding='utf-8').write(src)
    print("  ✅ Syntax OK, tallennettu")
except SyntaxError as e:
    print(f"  ❌ Syntaksivirhe: {e}")

# ═══════════════════════════════════════════════════════════
# FIX 2: Poista WordNet lemmatization EN Validatorista
# ═══════════════════════════════════════════════════════════
print("\n[2] EN VALIDATOR — WordNet-korjausten poisto\n")

try:
    src2 = open('en_validator.py', encoding='utf-8').read()

    # WordNet tekee vääriä korjauksia:
    # "have" → "bear", "bees" → "cost", "total" → "amount"
    # Ratkaisu: poista WordNet lemmatization ja synonyymi-lookup
    # Pidetään VAIN domain-synonyymit (ne toimivat hyvin: 9/12 OK)

    # Etsi validate()-metodi ja poista WordNet-vaiheet
    # Strategia: lisää flagi joka ohittaa WordNet-osuudet

    if 'use_wordnet' not in src2:
        # Lisää __init__:iin use_wordnet=False
        old_init = "class ENValidator:"
        # Etsi tarkempi kohta
        if "def __init__" in src2:
            # Lisää WordNet disable kommentti
            old_wn_check = "self.wordnet = WordNetEngine()"
            new_wn_check = "self.wordnet = WordNetEngine()\n        self.use_wordnet = False  # HOTFIX: WordNet tekee vääriä korjauksia"

            if old_wn_check in src2:
                src2 = src2.replace(old_wn_check, new_wn_check, 1)
                print("  ✅ WordNet asetettu use_wordnet=False")
            else:
                print("  ⚠️  WordNet init -riviä ei löydy")

            # Etsi kohdat joissa WordNet-tuloksia käytetään
            # ja lisää tarkistus
            old_wn_lemma = "if self.wordnet.available:"
            new_wn_lemma = "if self.wordnet.available and self.use_wordnet:"

            count = src2.count(old_wn_lemma)
            if count > 0:
                src2 = src2.replace(old_wn_lemma, new_wn_lemma)
                print(f"  ✅ {count} WordNet-tarkistusta ehdollistettu use_wordnet-lipulla")
            else:
                print("  ⚠️  WordNet.available -tarkistuksia ei löydy")

            try:
                ast.parse(src2)
                open('en_validator.py', 'w', encoding='utf-8').write(src2)
                print("  ✅ Syntax OK, tallennettu")
            except SyntaxError as e:
                print(f"  ❌ Syntaksivirhe: {e}")
        else:
            print("  ⚠️  ENValidator __init__ ei löydy")
    else:
        print("  ℹ️  use_wordnet jo olemassa")

except FileNotFoundError:
    print("  ❌ en_validator.py ei löydy")
except Exception as e:
    print(f"  ❌ Virhe: {e}")

# ═══════════════════════════════════════════════════════════
# VERIFIOINTI
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[3] VERIFIOINTI")
print("=" * 60)

# Testi 1: EN→FI ei enää tuota "päällä"
print("\n  EN→FI testi:")
try:
    # Reimport
    import importlib
    import translation_proxy as tp_mod
    importlib.reload(tp_mod)
    proxy = tp_mod.TranslationProxy()

    test_cases = [
        "Varroa treatment threshold is 3 mites per 100 bees in August.",
        "American foulbrood is a notifiable disease.",
        "The bee is on the flower collecting nectar.",
        "Oxalic acid is applied once in October.",
        "The beekeeper inspects hives on warm spring mornings.",
    ]

    all_ok = True
    for en in test_cases:
        r = proxy.en_to_fi(en)
        has_bug = "päällä" in r.text.lower()
        tag = "❌" if has_bug else "✅"
        if has_bug:
            all_ok = False
        print(f"    {tag} {en[:50]}...")
        print(f"       → {r.text[:70]}...")

    if all_ok:
        print("\n  ✅ 'päällä'-bugi KORJATTU!")
    else:
        print("\n  ❌ 'päällä'-bugi EDELLEEN — tarkista manuaalisesti")

except Exception as e:
    print(f"  ❌ Virhe testatessa: {e}")

# Testi 2: EN Validator ei tee vääriä korjauksia
print("\n  EN Validator testi:")
try:
    import en_validator as ev_mod
    importlib.reload(ev_mod)
    val = ev_mod.ENValidator()

    val_cases = [
        ("Honeybees have 5 eyes total", False),  # EI SAA muuttua
        ("The bees are in the hive", False),      # EI SAA muuttua
        ("Use remedy for bug sickness", True),     # PITÄÄ korjata
        ("Check the mother bee", True),            # PITÄÄ korjata
    ]

    for text, should_change in val_cases:
        r = val.validate(text)
        if should_change:
            tag = "✅" if r.was_corrected else "❌"
        else:
            tag = "✅" if not r.was_corrected else f"❌ muutti: {r.corrected}"
        print(f"    {tag} '{text}' → {'korjattu' if r.was_corrected else 'ei muutosta'}")

except Exception as e:
    print(f"  ❌ Virhe: {e}")

print(f"""
{'=' * 60}
  HOTFIX VALMIS
{'=' * 60}

  Muutokset:
  1. EN→FI: 50+ lyhyttä sanaa ohitetaan (is, on, or, and...)
     → "threshold is 3" pysyy englantina, ei "threshold päällä 3"

  2. EN Validator: WordNet poistettu käytöstä
     → "have" ei muutu "bear":ksi
     → "bees" ei muutu "cost":ksi
     → Domain-synonyymit toimivat edelleen (remedy→treatment)

  Aja: python test_pipeline.py
  uudelleen ja vertaa tuloksia!
""")

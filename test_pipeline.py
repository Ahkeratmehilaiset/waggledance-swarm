#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WaggleDance — Translation Pipeline Diagnostic v2.0
====================================================
Perusteellinen testi koko käännösputkelle:

  [1] Komponenttien lataus
  [2] FI→EN käännös (Voikko + sanakirja) — 20 lausetta ääkkösillä
  [3] EN Validator (domain-synonyymit) — 12 lausetta
  [4] EN→FI dict (word boundary -bugi) — 15 lausetta
  [5] EN→FI Opus-MT (kokonainen lause) — 8 lausetta
  [6] Round-trip: FI→EN→malli→EN Validator→EN→FI — 10 lausetta
  [7] Ääkkös-stressitesti: erikoismerkit läpi putken
  [8] Sanakirjan kattavuusanalyysi
  [9] YAML-sielun kielitunnistus
  [10] Yhteenveto ja diagnoosi
"""
import time
import sys
import os

# Windows cp1252 fix — force UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SEP = "=" * 72
SUBSEP = "-" * 72

# Kielletyt merkkijonot jotka viittaavat word boundary -bugiin
# Case-sensitive: "silmÄ" (iso Ä keskellä sanaa) on bugi, "silmä" on normaali suomi
FORBIDDEN_CASE_SENSITIVE = [
    "silmÄ", "mehilÄ", "silmÃ", "mehilÃ",  # Iso kirjain keskellä = korruptoitunut
    "Ã¤", "Ã¶", "Ã¥",                        # Double-encoded UTF-8
]
# Case-insensitive patterns
FORBIDDEN_CASE_INSENSITIVE = [
    "compää", "luumiis", "äällä",
]

def check_garbage(text):
    """Palauta lista löydetyistä ongelmista."""
    problems = []
    # Case-sensitive: detect corrupted characters (uppercase Ä/Ã mid-word)
    for bad in FORBIDDEN_CASE_SENSITIVE:
        if bad in text:
            problems.append(bad)
    # Case-insensitive: detect nonsense substrings
    for bad in FORBIDDEN_CASE_INSENSITIVE:
        if bad.lower() in text.lower():
            problems.append(bad)
    # Tarkista mojibake (double-encoded UTF-8)
    if "Ã" in text:
        problems.append("mojibake(Ã)")
    return problems


def score_keywords(text, keywords):
    """Laske kuinka moni avainsana löytyy tekstistä."""
    found = sum(1 for kw in keywords if kw.lower() in text.lower())
    return found, len(keywords)


print(SEP)
print("  🐝 WaggleDance Translation Pipeline Diagnostic v2.0")
print(SEP)

# ═══════════════════════════════════════════════════════════════
# [1] KOMPONENTTIEN LATAUS
# ═══════════════════════════════════════════════════════════════
print(f"\n[1] KOMPONENTTIEN LATAUS\n{SUBSEP}")

proxy = None
validator = None
yaml_bridge = None

try:
    from translation_proxy import TranslationProxy
    proxy = TranslationProxy()
    v = proxy.voikko
    print(f"  ✅ Translation Proxy")
    print(f"     Voikko:       {'✅ OK' if v.available else '❌ EI'}")
    print(f"     FI→EN dict:   {len(proxy.dict_fi_en)} termiä")
    print(f"     EN→FI dict:   {len(proxy.dict_en_fi)} termiä")
    print(f"     Opus-MT:      {'✅ OK' if proxy.opus.available else '❌ EI (vain sanakirja)'}")
except Exception as e:
    print(f"  ❌ Translation Proxy: {e}")

try:
    from en_validator import ENValidator
    domain = set(proxy.dict_en_fi.keys()) if proxy else set()
    validator = ENValidator(domain_terms=domain)
    wn = validator.wordnet.available if hasattr(validator, 'wordnet') else False
    syns = len(validator.domain_synonyms) if hasattr(validator, 'domain_synonyms') else 0
    print(f"  ✅ EN Validator (WordNet={'✅' if wn else '❌'}, Synonyymit={syns})")
except Exception as e:
    print(f"  ❌ EN Validator: {e}")

try:
    from core.yaml_bridge import YAMLBridge
    yaml_bridge = YAMLBridge("agents")
    yaml_bridge._ensure_loaded()
    has_en = hasattr(yaml_bridge, '_agents_en')
    has_proxy = hasattr(yaml_bridge, 'set_translation_proxy')
    print(f"  ✅ YAMLBridge ({len(yaml_bridge._agents)} agenttia, EN-tuki={'✅' if has_proxy else '❌'})")
except Exception as e:
    print(f"  ❌ YAMLBridge: {e}")

# ═══════════════════════════════════════════════════════════════
# [2] FI→EN KÄÄNNÖS — 20 lausetta ääkkösillä
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[2] FI → EN KÄÄNNÖS (Voikko + sanakirja) — 20 lausetta\n{SUBSEP}")

fi_tests = [
    # Perus mehiläistieto
    ("Mehiläisellä on viisi silmää", ["bee", "five", "eyes"]),
    ("Kuinka monta silmää on mehiläisellä", ["how", "many", "eyes", "bee"]),
    ("Kuningatar erittää feromoneja", ["queen", "secretes", "pheromones"]),
    ("Työläiset hoitavat toukkia emomaidolla", ["workers", "larvae", "royal", "jelly"]),
    # Varroa ja taudit
    ("Varroa-punkkien hoitokynnys on 3 prosenttia", ["varroa", "treatment", "threshold", "3"]),
    ("Amerikkalainen toukkamätä on ilmoitettava tauti", ["american", "foulbrood", "notifiable"]),
    ("Oksaalihappohoito tehdään lokakuussa", ["oxalic", "acid", "treatment", "october"]),
    ("Muurahaishappo haihduttaa varroapunkit", ["formic", "acid", "varroa"]),
    # Kausitieto
    ("Syysruokinta aloitetaan elokuussa sokeriliuoksella", ["autumn", "feeding", "august", "sugar", "syrup"]),
    ("Talvipallo muodostuu kun lämpötila laskee alle 14 asteeseen", ["winter", "cluster", "temperature", "14"]),
    ("Kevättarkastus tehdään kun lämpö ylittää 10 astetta", ["spring", "inspection", "temperature", "10"]),
    ("Hunajan kosteus ei saa ylittää 18 prosenttia", ["honey", "moisture", "18", "percent"]),
    # Kasvillisuus
    ("Maitohorsma kukkii heinä-elokuussa", ["willowherb", "flowering", "july"]),
    ("Vadelma on Suomen suosituin lajihunajan lähde", ["raspberry", "finland", "honey"]),
    ("Valkoapila erittää mettä lämpimillä öillä", ["white", "clover", "nectar", "warm"]),
    ("Kanerva kukkii elo-syyskuussa", ["heather", "flowering", "august"]),
    # Pesänhoito
    ("Pesän minimipaino keväällä on 15 kiloa", ["hive", "minimum", "weight", "spring", "15"]),
    ("Emottoman pesän tunnistaa hajakuviollisesta muninnasta", ["queenless", "colony", "scattered", "egg"]),
    ("Parveiluntarkistus tehdään seitsemän päivän välein", ["swarm", "check", "seven", "days"]),
    ("Langstroth-kehyksillä hoitomalli toimii tehokkaasti", ["langstroth", "frame"]),
]

fi_ok = 0
fi_weak = 0
fi_fail = 0

for fi_input, expected in fi_tests:
    if not proxy:
        print("  ⏭️  SKIP — ei proxyä")
        break
    t0 = time.perf_counter()
    r = proxy.fi_to_en(fi_input)
    dt = (time.perf_counter() - t0) * 1000

    found, total = score_keywords(r.text, expected)
    pct = found / total * 100 if total > 0 else 0

    if pct >= 60:
        fi_ok += 1
        tag = "✅"
    elif pct >= 30:
        fi_weak += 1
        tag = "⚠️"
    else:
        fi_fail += 1
        tag = "❌"

    print(f"\n  {tag} FI: {fi_input}")
    print(f"     EN: {r.text}")
    print(f"     [{r.method}, {dt:.1f}ms, coverage={r.coverage:.0%}] "
          f"Avainsanat: {found}/{total} ({pct:.0f}%)")

print(f"\n  📊 FI→EN: {fi_ok}✅ {fi_weak}⚠️ {fi_fail}❌ / {len(fi_tests)}")

# ═══════════════════════════════════════════════════════════════
# [3] EN VALIDATOR — 12 lausetta
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[3] EN VALIDATOR (domain-synonyymit) — 12 lausetta\n{SUBSEP}")

en_val_tests = [
    # Pitää korjata
    ("Use acid bath remedy for bee sickness", True, ["treatment", "disease"]),
    ("The bugs need sugar water for fall feeding", True, ["bees", "sugar syrup", "autumn"]),
    ("Check the mother bee and baby bees", True, ["queen", "brood"]),
    ("Fix the circuit breaker in the steam room", True, ["fuse", "sauna"]),
    ("The bee plague spread through the colony", True, ["foulbrood"]),
    ("Apply the remedy during cold season", True, ["treatment"]),
    ("The insect keeper checked the bug houses", True, ["beekeeper", "hives"]),
    # EI SAA muuttua
    ("Varroa treatment threshold is 3%", False, []),
    ("Queen secretes QMP pheromone", False, []),
    ("Inspect the brood frames in spring", False, []),
    ("Apply oxalic acid during broodless period", False, []),
    ("Winter cluster core temperature is 20 degrees", False, []),
]

val_ok = 0
val_fail = 0

for en_input, should_change, expected_kw in en_val_tests:
    if not validator:
        print("  ⏭️  SKIP — ei validaattoria")
        break
    r = validator.validate(en_input)

    if should_change:
        found, total = score_keywords(r.corrected, expected_kw)
        ok = r.was_corrected and found >= total * 0.5
    else:
        ok = not r.was_corrected

    if ok:
        val_ok += 1
        tag = "✅"
    else:
        val_fail += 1
        tag = "❌"

    if r.was_corrected:
        print(f"\n  {tag} IN:  {en_input}")
        print(f"     OUT: {r.corrected}")
        print(f"     [{r.method}, {r.latency_ms:.2f}ms, {r.correction_count} korjausta]")
    else:
        print(f"\n  {tag} IN:  {en_input}")
        print(f"     OUT: (ei muutosta)")

print(f"\n  📊 EN Validator: {val_ok}✅ {val_fail}❌ / {len(en_val_tests)}")

# ═══════════════════════════════════════════════════════════════
# [4] EN→FI DICT — 15 lausetta (word boundary)
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[4] EN → FI KÄÄNNÖS (sanakirja + word boundary) — 15 lausetta\n{SUBSEP}")

en_fi_tests = [
    "Honeybees have 5 eyes: 2 compound eyes and 3 ocelli.",
    "The queen secretes QMP pheromone to suppress worker ovaries.",
    "Varroa treatment threshold is 3 mites per 100 bees in August.",
    "Use formic acid treatment in late summer for varroa control.",
    "Winter cluster core temperature should be maintained at 20 degrees.",
    "The compound eyes detect movement and the ocelli measure light.",
    "Honey moisture must not exceed 18 percent for extraction.",
    "American foulbrood is a notifiable disease requiring immediate action.",
    "The bee is on the flower collecting nectar from blossoms.",
    "Autumn feeding provides 15-20 kg sugar syrup per colony.",
    "Willowherb and raspberry are the main nectar plants in Finland.",
    "Brood frame count should be at least 3 in April for a strong colony.",
    "The beekeeper inspects hives on warm spring mornings.",
    "Drone congregation areas are where queens mate during flight.",
    "Oxalic acid is applied once in October during the broodless period.",
]

enfi_ok = 0
enfi_fail = 0

for en_input in en_fi_tests:
    if not proxy:
        print("  ⏭️  SKIP")
        break
    r = proxy.en_to_fi(en_input)
    problems = check_garbage(r.text)

    if problems:
        enfi_fail += 1
        tag = "❌"
        extra = f" BUGI: {', '.join(problems)}"
    else:
        enfi_ok += 1
        tag = "✅"
        extra = ""

    print(f"\n  {tag} EN: {en_input}")
    print(f"     FI: {r.text}")
    print(f"     [{r.method}, {r.latency_ms:.1f}ms]{extra}")

print(f"\n  📊 EN→FI: {enfi_ok}✅ {enfi_fail}❌ / {len(en_fi_tests)}")

# ═══════════════════════════════════════════════════════════════
# [5] OPUS-MT FALLBACK — 8 lausetta
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[5] OPUS-MT FALLBACK (kokonainen lause) — 8 lausetta\n{SUBSEP}")

opus_tests = [
    "Honeybees have 5 eyes: 2 compound eyes and 3 ocelli.",
    "The varroa treatment threshold is 3 mites per 100 bees.",
    "Autumn feeding should provide 15-20 kg of sugar syrup per colony.",
    "American foulbrood is a notifiable disease in Finland.",
    "The queen controls the colony through QMP pheromone.",
    "Willowherb blooms in July and August producing water-white honey.",
    "The winter cluster maintains a core temperature of 20 degrees when broodless.",
    "Worker bees transition through age polyethism from nurse to forager.",
]

opus_ok = False
if proxy and proxy.opus.available:
    opus_ok = True
    for en_input in opus_tests:
        r = proxy.en_to_fi(en_input, force_opus=True)
        problems = check_garbage(r.text if r else "")
        tag = "✅" if not problems else "❌"
        print(f"\n  {tag} EN: {en_input}")
        print(f"     FI: {r.text if r else 'TYHJÄ'}")
        print(f"     [{r.method if r else '?'}, {r.latency_ms if r else 0:.1f}ms]")
else:
    print("""
  ❌ Opus-MT EI SAATAVILLA

  Tämä on KRIITTINEN puute:
    → EN→FI käännös käyttää VAIN sanakirjaa (termi→termi)
    → Kokonaisten lauseiden kielioppi hajoaa
    → Sanakirja korvaa "treatment" → "hoito" mutta ei taivuta
    → Tulos: "Mehiläinen hoito kynnys on 3 punkki per 100 mehiläinen"

  Sanakirjakäännös toimii VAIN yksittäisille termeille,
  EI kokonaisille lauseille!
""")

# ═══════════════════════════════════════════════════════════════
# [6] ROUND-TRIP — 10 lausetta
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[6] ROUND-TRIP: FI → EN → malli(sim) → Validator → EN → FI\n{SUBSEP}")

roundtrip_tests = [
    ("Kuinka monta silmää on mehiläisellä?",
     "Honeybees have 5 eyes total: 2 compound eyes and 3 ocelli for measuring light intensity."),
    ("Mikä on varroa-hoitokynnys?",
     "The varroa treatment threshold is 3 mites per 100 bees in August."),
    ("Milloin syysruokinta aloitetaan?",
     "Autumn feeding should start by week 34 with 15-20 kg sugar syrup per colony."),
    ("Mitä tehdään amerikkalaisen toukkamädän epäilyssä?",
     "If AFB is suspected: do not move frames, report to Food Authority, isolate hive immediately."),
    ("Mitä kasveja kukkii Mäntyharjulla?",
     "Main nectar plants in central Finland: willowherb, raspberry, white clover, and heather."),
    ("Miten emottoman pesän tunnistaa?",
     "A queenless colony shows no brood, scattered egg pattern, and increased aggression."),
    ("Mikä on talvipallon ydinlämpötila?",
     "Winter cluster core temperature is 20 degrees Celsius when broodless and 34.5 when brooding."),
    ("Paljonko sokeria syysruokintaan per pesä?",
     "Autumn feeding requires 15-20 kg of sugar per colony in zone II-III of Finland."),
    ("Milloin oksaalihappohoito tehdään?",
     "Oxalic acid treatment is applied in October-November during the broodless period."),
    ("Mikä on hunajan kosteusyläraja?",
     "Honey moisture must not exceed 18 percent before extraction to prevent fermentation."),
]

rt_ok = 0
rt_fail = 0

for fi_question, simulated_en in roundtrip_tests:
    if not proxy:
        break

    # Vaihe A: FI→EN
    r1 = proxy.fi_to_en(fi_question)

    # Vaihe B: EN Validator
    validated_en = simulated_en
    val_info = ""
    if validator:
        vr = validator.validate(simulated_en)
        validated_en = vr.corrected
        if vr.was_corrected:
            val_info = f" → Validator: {vr.correction_count} korjausta"

    # Vaihe C: EN→FI
    r3 = proxy.en_to_fi(validated_en)

    # Laatu
    problems = check_garbage(r3.text)
    if problems:
        rt_fail += 1
        quality = f"❌ FAIL: {', '.join(problems)}"
    else:
        rt_ok += 1
        quality = "✅ luettavissa"

    print(f"\n  Käyttäjä:  {fi_question}")
    print(f"  → FI→EN:   {r1.text}")
    print(f"             [{r1.method}, {r1.latency_ms:.1f}ms, coverage={r1.coverage:.0%}]")
    print(f"  → Malli:   {simulated_en}")
    if val_info:
        print(f"  → Valid:   {validated_en}{val_info}")
    print(f"  → EN→FI:   {r3.text}")
    print(f"             [{r3.method}, {r3.latency_ms:.1f}ms]")
    print(f"  → Laatu:   {quality}")

print(f"\n  📊 Round-trip: {rt_ok}✅ {rt_fail}❌ / {len(roundtrip_tests)}")

# ═══════════════════════════════════════════════════════════════
# [7] ÄÄKKÖS-STRESSITESTI
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[7] ÄÄKKÖS-STRESSITESTI\n{SUBSEP}")

aakkos_tests = [
    "Päämehiläishoitaja tarkistaa pesät säännöllisesti",
    "Höyrysauna lämpenee öljykattilalla mökkialueella",
    "Kääriytyneessä vahapohjassa näkyy sikiöalueita",
    "Ötökkätuholaisten häätö onnistuu äänekkäästi",
    "Yhdyskuntien köyhyys näyttäytyy pieninä pesäkokoina",
]

for fi_input in aakkos_tests:
    if not proxy:
        break

    r = proxy.fi_to_en(fi_input)
    problems = check_garbage(r.text)

    # Tarkista säilyikö ääkköset jos ne ovat passthrough-sanoja
    has_fi_chars = any(c in r.text for c in "äöåÄÖÅ")

    tag = "❌" if problems else "✅"
    print(f"\n  {tag} FI: {fi_input}")
    print(f"     EN: {r.text}")
    print(f"     [{r.method}] Ääkköset EN:ssä: {'kyllä (passthrough)' if has_fi_chars else 'ei (käännetty)'}")
    if problems:
        print(f"     BUGI: {', '.join(problems)}")

# ═══════════════════════════════════════════════════════════════
# [8] SANAKIRJAN KATTAVUUSANALYYSI
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[8] SANAKIRJAN KATTAVUUSANALYYSI\n{SUBSEP}")

if proxy:
    # Lajittele yleisimmät termit
    fi_en = proxy.dict_fi_en
    en_fi = proxy.dict_en_fi

    print(f"\n  FI→EN sanakirja: {len(fi_en)} termiä")
    print(f"  EN→FI sanakirja: {len(en_fi)} termiä")

    # Kriittiset termit jotka PITÄÄ löytyä
    critical_fi = [
        "mehiläinen", "kuningatar", "työläinen", "kuhnuri",
        "varroa", "toukkamätä", "hunaja", "pesä",
        "sikiö", "kenno", "kehys", "vaha",
        "maitohorsma", "vadelma", "apila", "kanerva",
        "oksaalihappo", "muurahaishappo", "sokeriliuos",
        "talvipallo", "parveiltu", "emokenno",
    ]

    critical_en = [
        "bee", "honeybee", "queen", "worker", "drone",
        "varroa", "foulbrood", "honey", "hive", "colony",
        "brood", "comb", "frame", "wax",
        "willowherb", "raspberry", "clover", "heather",
        "oxalic acid", "formic acid", "sugar syrup",
        "winter cluster", "swarm", "queen cell",
    ]

    print(f"\n  Kriittiset FI→EN termit:")
    fi_found = 0
    fi_missing = []
    for term in critical_fi:
        if term in fi_en:
            fi_found += 1
        else:
            fi_missing.append(term)

    print(f"    Löytyy: {fi_found}/{len(critical_fi)}")
    if fi_missing:
        print(f"    Puuttuu: {', '.join(fi_missing)}")

    print(f"\n  Kriittiset EN→FI termit:")
    en_found = 0
    en_missing = []
    for term in critical_en:
        if term in en_fi:
            en_found += 1
        else:
            en_missing.append(term)

    print(f"    Löytyy: {en_found}/{len(critical_en)}")
    if en_missing:
        print(f"    Puuttuu: {', '.join(en_missing)}")

    # Vaarallisen lyhyet termit EN→FI sanakirjassa (word boundary riski)
    short_terms = [(k, v) for k, v in en_fi.items() if len(k) <= 3]
    if short_terms:
        print(f"\n  ⚠️  Lyhyet EN→FI termit ({len(short_terms)} kpl) — word boundary riski:")
        for k, v in sorted(short_terms, key=lambda x: len(x[0])):
            print(f"    '{k}' → '{v}'")

# ═══════════════════════════════════════════════════════════════
# [9] YAML-SIELUN KIELITUNNISTUS
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[9] YAML-SIELUN KIELITUNNISTUS\n{SUBSEP}")

if yaml_bridge and hasattr(yaml_bridge, '_detect_yaml_language'):
    fi_count = 0
    en_count = 0
    for agent_id, data in yaml_bridge._agents.items():
        lang = yaml_bridge._detect_yaml_language(data)
        if lang == "en":
            en_count += 1
        else:
            fi_count += 1

    print(f"\n  Yhteensä: {len(yaml_bridge._agents)} agenttia")
    print(f"  Suomeksi:   {fi_count} (käännetään EN lennossa)")
    print(f"  Englanniksi: {en_count} (käytetään sellaisenaan)")

    # Näytä 3 esimerkkiä
    for agent_id in list(yaml_bridge._agents.keys())[:3]:
        data = yaml_bridge._agents[agent_id]
        lang = yaml_bridge._detect_yaml_language(data)
        header = data.get("header", {})
        name = header.get("agent_name", agent_id)
        print(f"\n    {agent_id}: '{name}' → {lang}")

    # Testa EN-käännös yhdellä agentilla (koko käännös voi jumittua GPU:lla)
    if proxy and hasattr(yaml_bridge, '_translate_deep'):
        print(f"\n  Testaan EN-käännös (1 agentti)...")
        sample_id = list(yaml_bridge._agents.keys())[0]
        sample_data = yaml_bridge._agents[sample_id]
        import signal
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        def _translate_one():
            return yaml_bridge._translate_deep(sample_data, proxy)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_translate_one)
                translated_sample = fut.result(timeout=30)
            yaml_bridge._agents_en = {sample_id: translated_sample}
        except (FutTimeout, Exception) as e:
            print(f"    ⚠️ EN-käännös timeout/virhe: {e}")
            yaml_bridge._agents_en = {}
        if hasattr(yaml_bridge, '_agents_en') and yaml_bridge._agents_en:
            sample_id = list(yaml_bridge._agents_en.keys())[0]
            sample_en = yaml_bridge._agents_en[sample_id]
            header_en = sample_en.get("header", {})
            name_en = header_en.get("agent_name", "?")

            sample_fi = yaml_bridge._agents[sample_id]
            header_fi = sample_fi.get("header", {})
            name_fi = header_fi.get("agent_name", "?")

            print(f"    Esimerkki: '{name_fi}' → '{name_en}'")

            # Vertaa system prompt FI vs EN
            yaml_bridge._language = "fi"
            prompt_fi = yaml_bridge.build_system_prompt(sample_id)
            yaml_bridge._language = "en"
            prompt_en = yaml_bridge.build_system_prompt(sample_id)

            print(f"    FI prompt: {prompt_fi[:80]}...")
            print(f"    EN prompt: {prompt_en[:80]}...")
        else:
            print("    ❌ EN-käännösvälimuisti tyhjä")
else:
    print("  ⏭️  YAMLBridge ei saatavilla tai ei EN-tukea")

# ═══════════════════════════════════════════════════════════════
# [10] YHTEENVETO JA DIAGNOOSI
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}\n[10] YHTEENVETO JA DIAGNOOSI\n{SEP}")

print(f"""
  ┌─────────────────────────────────────────────────────────┐
  │ KOMPONENTIT                                             │
  │   Translation Proxy:  {'✅ OK' if proxy else '❌ PUUTTUU':40s}│
  │   Voikko:             {'✅ OK' if proxy and proxy.voikko.available else '❌ EI':40s}│
  │   EN Validator:       {'✅ OK' if validator else '❌ PUUTTUU':40s}│
  │   Opus-MT:            {'✅ OK' if opus_ok else '❌ EI':40s}│
  │   YAMLBridge EN:      {'✅ OK' if yaml_bridge and hasattr(yaml_bridge, 'set_translation_proxy') else '❌ EI':40s}│
  ├─────────────────────────────────────────────────────────┤
  │ TESTIT                                                  │
  │   FI→EN:              {fi_ok:2d}✅ {fi_weak:2d}⚠️  {fi_fail:2d}❌  / {len(fi_tests):2d}              │
  │   EN Validator:       {val_ok:2d}✅       {val_fail:2d}❌  / {len(en_val_tests):2d}              │
  │   EN→FI boundary:    {enfi_ok:2d}✅       {enfi_fail:2d}❌  / {len(en_fi_tests):2d}              │
  │   Round-trip:         {rt_ok:2d}✅       {rt_fail:2d}❌  / {len(roundtrip_tests):2d}              │
  └─────────────────────────────────────────────────────────┘
""")

# Diagnoosi
print("  DIAGNOOSI:")
print("  " + SUBSEP)

issues = []

if proxy and not proxy.opus.available:
    issues.append(("KRIITTINEN", "Opus-MT puuttuu",
        "EN→FI käyttää vain sanakirjaa → kokonaiset lauseet hajoavat.\n"
        "    Sanakirja korvaa termejä (treatment→hoito) mutta ei taivuta\n"
        "    eikä rakenna lauseita. Tulos on lista sanoja, ei suomea.\n"
        "    FIX: pip install transformers sentencepiece\n"
        "    TAI: anna mallin vastata suoraan suomeksi (poista EN→FI)"))

if enfi_fail > 0:
    issues.append(("VIRHE", f"Word boundary -bugi ({enfi_fail} FAIL)",
        "'on'→'päällä' korvaa myös sanojen sisältä.\n"
        "    FIX: varmista \\b word boundary regex EN→FI loopissa"))

if fi_fail > 5:
    issues.append(("VAROITUS", f"FI→EN heikko ({fi_fail} FAIL)",
        "Voikko+sanakirja ei kata tarpeeksi termejä.\n"
        "    FIX: laajenna domain-sanakirjaa"))

if not issues:
    print("\n  ✅ Putki toimii teknisesti!")
    if not opus_ok:
        print("     (mutta EN→FI laatu on heikko ilman Opus-MT:tä)")
else:
    for severity, title, detail in issues:
        print(f"\n  {'🔴' if severity == 'KRIITTINEN' else '🟡' if severity == 'VIRHE' else '🟠'} [{severity}] {title}")
        print(f"    {detail}")

print(f"""
  RATKAISUVAIHTOEHDOT:
  {'─' * 56}
  A) OPUS-MT (paras):
     pip install transformers sentencepiece
     → Kokonaiset lauseet kääntyvät oikein
     → ~300ms viive per vastaus

  B) MALLI VASTAA SUOMEKSI (nopein fix):
     → Poista EN→FI käännös kokonaan
     → Lisää system promptiin "Vastaa AINA suomeksi"
     → phi4-mini osaa perustason suomea
     → Ei käännösviivettä, ei käännösvirheitä

  C) ISOMPI MALLI (paras laatu):
     → gemma3:4b tai qwen2.5:7b joka osaa suomea
     → Ei tarvitse käännöstä ollenkaan
     → Vaatii enemmän VRAM:ia

  D) HYBRIDI (suositeltu):
     → EN sisäisesti (paras laatu LLM:ltä)
     → Opus-MT EN→FI lopussa (oikea suomi ulos)
     → Vaatii Opus-MT asennuksen
""")

# Standard summary for backup parser
total_pass = fi_ok + val_ok + enfi_ok + rt_ok
total_fail = fi_fail + val_fail + enfi_fail + rt_fail
total_warn = fi_weak
print(f"PASS: {total_pass}")
print(f"FAIL: {total_fail}")
print(f"WARN: {total_warn}")

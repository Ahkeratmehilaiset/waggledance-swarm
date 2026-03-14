# WaggleDance Session Summary — 2026-03-13/14
## For: Claude Opus (next session handoff)

---

## Mitä tehtiin tässä sessiossa (v1.9.0 → v1.15.0)

### Lähtötilanne
- v1.9.0 valmis: seasonal routing, 66 test suitea
- `test_fi_normalization.py` oli juuri luotu mutta 2 testiä feilasi + Unicode-kaatuminen

---

## Korjaukset ja parannukset (6 versiota)

### v1.10.0 — FI normalisointi SmartRouterV2:ssa
**Mitä havaittiin:** `_classify_keywords()` ei tunnistanut ASCII-muotoisia suomalaisia sanoja (esim. "lammitys" ≠ "lämmitys").
**Korjattiin:** Lisätty `_normalize_fi()` (ä→a, ö→o, å→a) SmartRouterV2:een. Kaikki keyword-haut tehdään sekä raa'alle että normalisoitulle kyselylle.
**Tiedosto:** `core/smart_router_v2.py`
**Testi:** `tests/test_fi_normalization.py` (8 testiä)

### v1.11.0 — Sanasanarajankorjaus capsulessa
**Mitä havaittiin:** `domain_capsule.py` käytti `re.escape(kw)` ilman sanarajan tarkistusta → "energi" osui sanaan "aurinkoenergia", "mite" osui sanaan "miten" (suomi: "how").
**Korjattiin:** Kaikki keyword-patternit käännetty muotoon `r'\b' + re.escape(kw)` (sanaalkuraja). Poistettu "mite" varroa_treatment-avainsanoista (false positive).
**Tiedosto:** `core/domain_capsule.py`, `configs/capsules/cottage.yaml`
**Testi:** `tests/test_capsule_word_boundary.py` (6 testiä)

### v1.12.0 — FI normalisointi capsule-tasolla
**Mitä havaittiin:** Capsule-matching teki vain exact-matchin — "lammitys" ei osannut "lämmitys"-avainsanaa, "jaatya" ei osannut "jäätyä".
**Korjattiin:** Lisätty paikallinen `_normalize_fi()` `domain_capsule.py`:hen (ei circular import). Jokainen keyword käännetään etukäteen sekä raa'aksi että normalisoiduksi patternksi. `match_decision()` testaa molemmat versiot.
**Tiedosto:** `core/domain_capsule.py`
**Testi:** `tests/test_capsule_fi_normalization.py` (7 testiä)
**Sivuvaikutus:** Poistettu "mehiläi" honey_yield-avainsanoista — liian geneerinen, aiheutti false positive seasonal-kyselyissä (huhtikuu).

### v1.13.0 — Default fallback unknown kyselyille
**Mitä havaittiin:** Step 4 (priority fallback) ohjasi tuntemattoman kyselyn `rule_constraints`-kerrokseen (korkein prioriteetti mutta väärä semantiikka catch-allille).
**Korjattiin:** `cottage.yaml`:iin lisätty `default_fallback: llm_reasoning`. `DomainCapsule` lukee kentän, `SmartRouterV2` Step 4 käyttää sitä. Jos fallback-kerros ei ole käytössä, otetaan ensimmäinen käytössä oleva.
**Tiedostot:** `core/domain_capsule.py`, `core/smart_router_v2.py`, `configs/capsules/cottage.yaml`
**Testi:** `tests/test_capsule_default_fallback.py` (5 testiä)

### v1.14.0 — Route() kutsutaan kerran per kysely
**Mitä havaittiin:** `chat_handler.py` kutsui `smart_router_v2.route(message)` 3–4 kertaa per kysely (rivit 247, 315, 358, 406 — jokainen kerros erikseen).
**Korjattiin:** Yksi `_route = None` -blokki ennen kaikkia kerrostarkistuksia. Kaikki layerit jakavat saman tuloksen. Lisätty reason-koodit: `"keyword_classifier:math/seasonal/rule/stat/retrieval"`.
**Tiedosto:** `core/chat_handler.py`, `core/smart_router_v2.py`
**Testi:** `tests/test_router_stats.py` (7 testiä)

### v1.15.0 — matched_keywords läpinäkyvyys
**Mitä havaittiin:** API-vastauksesta ei selvinnyt mikä avainsana laukaisi reitityksen — debug oli vaikeaa.
**Korjattiin:** `matched_keywords: list[str]` lisätty `RouteResult`- ja `DecisionMatch`-dataluokkiin. Capsule-match kerää osuvat avainsanat listaksi. Keyword-classifier palauttaa ensimmäisen osuman regex-ryhmästä. `to_dict()` sisältää kentän.
**Tiedostot:** `core/smart_router_v2.py`, `core/domain_capsule.py`
**Testi:** `tests/test_matched_keywords.py` (7 testiä)

---

## Yön aikana havaitut ongelmat (night monitor)

### Ongelma 1: Oppiminen jäätyi ~6h (14:00–21:35)
- Facts juuttui 3979:ään vaikka heartbeat jatkoi (HB 3875→5701)
- **Syy:** `self_generate` saavutti konvergenssin ja istunto päättyi. Uusi istunto ei käynnisty automaattisesti.
- **Status:** EI KORJATTU — korjausehdokas seuraavaan sprinttiin

### Ongelma 2: Prosessi kaatui ~09:00 (15h downtime)
- 30 peräkkäistä API timeout -ilmoitusta 09:03–00:00
- Syy tuntematon — todennäköisesti manuaalinen sammutus tai OOM
- **Status:** EI TUTKITTU

### Ongelma 3: Disk laski 5.3 GB → 4.4 GB uudelleenkäynnistyksen yhteydessä
- Positiivinen: 0.9 GB vapautui (WAL-checkpoint tai tmp-siivous)
- Cur kasvoi: 11657 → 12402 (+745 uutta curated-paria edellisessä sessiossa)

---

## Lopputilanne (2026-03-14 ~08:38)

| Mittari | Arvo |
|---------|------|
| Versio | v1.15.0 |
| Test suitet | **72/72 PASS** |
| Testejä yhteensä | **944 ok, 17 warn, 0 fail** |
| Health Score | **100/100** |
| Backup | waggle_20260314_001131.zip (459.8 MB) |
| Curated pairs | ~12 402 |
| Night learning | Käynnissä, pass 52%, 1294 faktaa/8.5h |

---

## Seuraavan session parannusehdotukset (prioriteettijärjestyksessä)

### P1 — Night learning: automaattinen istunnon resetointi
**Ongelma:** self_generate pysähtyy konvergenssin vuoksi eikä käynnisty uudelleen — 6h hukkaa
**Ehdotus:** NightEnricher: jos kaikki lähteet pausella >30min, resetoi konvergenssihistoria ja aloita uusi enrichment-istunto

### P2 — Oppimisen jäätymisen monitorointi
**Ongelma:** Facts-laskuri ei kasva mutta syytä ei tiedetä
**Ehdotus:** Lisää `learning_stall_detected`-metriikka: jos Facts ei kasva 20+ HB, logita varoitus

### P3 — Curated data -laatu
**Havainto:** Cur 11657→12402 (+745) mutta Rej myös kasvoi (+390) — hylkäysaste korkea
**Ehdotus:** Tarkista onko quality gate liian tiukka vai onko self_generate tuottamassa heikkoja pareja

### P4 — Prosessin automaattinen uudelleenkäynnistys
**Ongelma:** 15h downtime ilman automaattista toipumista
**Ehdotus:** Windows Task Scheduler tai NSSM-palvelu automaattiseen uudelleenkäynnistykseen kaatumisen jälkeen

### P5 — SmartRouter v2: Step 5 fallback-kerroshierarkia
**Havainto:** default_fallback on nyt staattinen ("llm_reasoning") — voisi olla dynaaminen (A/B-testi eri fallback-strategioiden välillä)

---

## Arkkitehtuurin tila

```
ChatHandler routing pipeline (v1.15.0):
  _route = smart_router_v2.route(message)  ← kerran, jaetaan kaikille kerroksille

  Step 1: HotCache hit → retrieval
  Step 2: Capsule match (≥0.1 conf, \b boundary, FI+ASCII patterns) → model_based / rule / retrieval
  Step 3: Keyword classifier (math/seasonal/rule/stat/retrieval) → reason_code + matched_keywords
  Step 4: default_fallback = "llm_reasoning" (cottage.yaml)
  Step 5: FI→EN + WEIGHTED_ROUTING agent dispatch
  Step 6: LLM direct
```

```
DomainCapsule (v1.12.0):
  For each keyword:
    raw_pattern = re.compile(r'\b' + re.escape(kw.lower()))
    norm_pattern = re.compile(r'\b' + re.escape(_normalize_fi(kw.lower())))
  match_decision(query):
    q_lower, q_norm = query.lower(), _normalize_fi(query.lower())
    hit_kws = [kw for kw,p,pn in ... if p.search(q_lower) or pn.search(q_norm)]
    → DecisionMatch(layer, confidence, matched_keywords=hit_kws)
```

---

*Generoitu: 2026-03-14 | Sessio: v1.9.0 → v1.15.0 | 6 versiota, 6 uutta test suitea*

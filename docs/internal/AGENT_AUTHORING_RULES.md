# AGENT_AUTHORING_RULES.md
# OpenClaw v1.4 — Agentin kirjoitussäännöt (NORMATIIVINEN)

## TÄMÄ TIEDOSTO ON PAKOLLINEN OSA JÄRJESTELMÄÄ
Jokaisen agentin core.yaml TULEE noudattaa näitä sääntöjä.
Validointiskripti `tools/validate.py` tarkistaa automaattisesti.

---

## 1. SYVYYSVAATIMUS (PAKOLLINEN)

Jokaisen agentin YAML:ssä ON OLTAVA:

| Osio | Minimiarvo | Validointi |
|------|-----------|-----------|
| `DECISION_METRICS_AND_THRESHOLDS` | ≥ 5 metriikkaa | Automaattinen |
| → joista `action`-kentällisiä | ≥ 3 kpl | Automaattinen |
| → joissa **numeerinen arvo** | ≥ 3 kpl | Automaattinen |
| `SEASONAL_RULES` | 4 kautta (kevät/kesä/syksy/talvi) | Automaattinen |
| → joissa **spesifinen toimenpide** | ≥ 2 kautta | Manuaalinen |
| `FAILURE_MODES` | ≥ 2 vikatilannetta | Automaattinen |
| → joissa detection + action | Pakollinen | Automaattinen |
| `eval_questions` | ≥ 30 kpl | Automaattinen |
| `COMPLIANCE_AND_LEGAL` | Kaikki relevantit lait | Manuaalinen |
| Lähdeviitteet `[src:ID]` | Jokaisella raja-arvolla | Manuaalinen |

---

## 2. KIELLETYT ILMAISUT

Seuraavat ilmaisut ovat **KIELLETTYJÄ** agentin operatiivisessa tiedossa:

❌ "Agentti seuraa tilannetta ja raportoi"
❌ "Tarkista tarvittaessa"
❌ "Ota yhteyttä asiantuntijaan"
❌ "Huolehdi asianmukaisesti"
❌ "Seuraa säännöllisesti"

✅ Sen sijaan:

| Kielletty | Vaadittava korvike |
|-----------|-------------------|
| "seuraa tilannetta" | "mittaa X 15min välein, hälytys jos >Y" |
| "tarkista tarvittaessa" | "tarkista 3kk välein tai kun Z tapahtuu" |
| "ota yhteyttä asiantuntijaan" | "ilmoita [agentti_id]:lle P1-hälytys" |
| "huolehdi asianmukaisesti" | "varmista arvo välillä X-Y, kirjaa poikkeama" |
| "seuraa säännöllisesti" | "mittaus viikoittain vko 20-35, 2x/kk muuten" |

---

## 3. METRIIKAT: NUMEROT PAKOLLISIA

Jokainen `DECISION_METRICS_AND_THRESHOLDS`-kenttä TULEE sisältää:

```yaml
metric_name:
  value: <NUMEERINEN ARVO TAI ALUE>     # PAKOLLINEN
  unit: <YKSIKKÖ>                        # SUOSITELTU
  action: <MITÄ TEHDÄÄN KUN YLITTYY>     # PAKOLLINEN (≥3 per agentti)
  source: "src:XX"                       # PAKOLLINEN
```

Jos numeerista arvoa EI TIEDETÄ:

```yaml
metric_name:
  value: "UNKNOWN"
  reason: "Ei saatavilla julkisista lähteistä"
  to_verify: "Kysy [organisaatio]:lta / mittaa paikan päällä"
  source: "src:XX"
```

**ÄLÄ KEKSI LUKUJA.** Väärä raja-arvo on vaarallisempi kuin puuttuva.

---

## 4. REFERENSSIAGENTTI: Tarhaaja (Päämehiläishoitaja)

**KAIKKIEN agenttien on oltava vähintään Tarhaajan syvyystasolla.**

Tarhaajan esimerkkisyvyys:

```yaml
DECISION_METRICS_AND_THRESHOLDS:
  varroa_per_100_bees:
    value: 3                              # ← NUMERO
    action: ">3 → välitön kemiallinen hoito"  # ← SPESIFINEN TOIMENPIDE
    source: "src:TAR1"                     # ← LÄHDEVIITE
  spring_weight_kg:
    value: 15                              # ← NUMERO
    action: "<15 kg → hätäruokinta 1:1 sokeriliuos"  # ← TARKKA OHJE
    source: "src:TAR1"
  winter_cluster_temp_c:
    value: 20                              # ← NUMERO
    action: "<20°C → KRIITTINEN hälytys"   # ← PRIORITEETTI
    source: "src:TAR1"
```

Vertaa HEIKKOON agenttiin:

```yaml
# ❌ HYLÄTTY - liian kuvaileava
DECISION_METRICS_AND_THRESHOLDS:
  temperature:
    value: "Jatkuva seuranta"             # ← EI NUMEROA
    # ← EI action-KENTTÄÄ
    source: "src:XX"
```

---

## 5. KAUSIKOHTAISET SÄÄNNÖT: SPESIFISYYSVAATIMUS

Jokainen `SEASONAL_RULES`-sääntö TULEE sisältää:
- **Ajankohta** (viikot tai kuukaudet)
- **Mitattava toimenpide** (ei pelkkä kuvaus)
- **Kytkentä muihin agentteihin** (jos relevantti)

```yaml
# ✅ HYVÄKSYTTY
- season: Kevät
  action: >
    Varroa-alkumittaus vko 18-20 sokeripudistuksella.
    Jos >3/100 → amitraz-hoito ennen satokukkaa.
    Ilmoita fenologille kukinta-ajankohdasta.
  source: "src:TAR1"

# ❌ HYLÄTTY
- season: Kevät
  action: "Kevään tarkistukset ja seuranta."
  source: "src:TAR1"
```

---

## 6. FAILURE MODES: KOLMIPORTAINEN RAKENNE

Jokainen vikatilannekenttä TULEE sisältää:

```yaml
- mode: <Mitä tapahtuu>
  detection: <Miten havaitaan (konkreettinen)>
  action: <Mitä tehdään (askel askeleelta)>
  priority: <P1/P2/P3>
  notify: [<agentti_id_1>, <agentti_id_2>]
  source: "src:XX"
```

---

## 7. LÄHDEVAATIMUKSET

| Lähdetyyppi | Hyväksytty | Ei hyväksytty |
|-------------|-----------|--------------|
| Viranomainen | Ruokavirasto, Luke, Tukes, IL, THL, SYKE | |
| Laki | Finlex (ajantasainen lainsäädäntö) | Vanhentunut laki |
| Alan järjestö | SML, RIL, Nuohousalan Keskusliitto | Wikipedia, blogi |
| Standardi | SFS, EN, ISO | |
| Valmistaja | Virallinen tekninen dokumentaatio | Mainoslehti |

Jokainen lähde vaatii:
```yaml
sources:
  - id: "src:XX"
    org: "Organisaatio"
    title: "Dokumentin nimi"
    year: 2025
    url: "https://..."
    supports: "Mitä tietoa tukee"
```

---

## 8. VALIDOINTISKRIPTIN KATTAVUUS

`tools/validate.py` tarkistaa AUTOMAATTISESTI:

| Tarkistus | Hylkäysraja |
|-----------|------------|
| Metriikat | < 5 |
| Action-kentälliset metriikat | < 3 |
| Numeerisia arvoja sisältävät metriikat | < 3 |
| Kausikohtaiset säännöt | < 4 |
| Vikatilanteet | < 2 |
| Eval-kysymykset | < 30 |
| Lähdetiedosto olemassa | Puuttuu |

---

## 9. AGENTIN KIRJOITTAMISEN TARKISTUSLISTA

Ennen agentin luovuttamista, tarkista:

- [ ] Onko jokaisella metriikalla numeerinen arvo (tai UNKNOWN + reason)?
- [ ] Onko vähintään 3 metriikalla `action`-kenttä numeerisella kynnysarvolla?
- [ ] Ovatko kausikohtaiset säännöt spesifisiä (viikot, lämpötilat, toimenpiteet)?
- [ ] Onko vähintään 2 failure modea, joissa detection + action?
- [ ] Viittaako jokainen raja-arvo lähteeseen [src:ID]?
- [ ] Onko lähdetiedosto (sources.yaml) olemassa ja kattava?
- [ ] Onko vähintään 30 eval-kysymystä, jotka testaavat päätöksentekoa?
- [ ] Onko KIELLETYT ILMAISUT (kohta 2) poistettu?
- [ ] Onko syvyys vähintään Tarhaajan tasolla?

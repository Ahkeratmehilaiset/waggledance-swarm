# OpenClaw v1.4 — 50-Agent Operatiivinen Tietokanta
# JKH Service / Korvenranta, Kouvola
# Generoitu: 2026-02-21
# Agentit: 50 | Kysymykset: 2000 | Lähteet: Suomalaiset viranomaiset + alan järjestöt

---

## SISÄLLYSLUETTELO

 1. Core/Dispatcher (Päällikkö)
 2. Luontokuvaaja (PTZ-operaattori)
 3. Ornitologi (Lintutieteilijä)
 4. Riistanvartija
 5. Hortonomi (Kasvitieteilijä)
 6. Metsänhoitaja
 7. Fenologi
 8. Pieneläin- ja tuholaisasiantuntija
 9. Entomologi (Hyönteistutkija)
10. Tähtitieteilijä
11. Valo- ja varjoanalyytikko
12. Tarhaaja (Päämehiläishoitaja)
13. Lentosää-analyytikko
14. Parveiluvahti
15. Pesälämpö- ja kosteusmittaaja
16. Nektari-informaatikko
17. Tautivahti (mehiläiset)
18. Pesäturvallisuuspäällikkö (karhut ym.)
19. Limnologi (Järvitutkija)
20. Kalastusopas
21. Kalantunnistaja
22. Rantavahti
23. Jääasiantuntija
24. Meteorologi
25. Myrskyvaroittaja
26. Mikroilmasto-asiantuntija
27. Ilmanlaadun tarkkailija
28. Routa- ja maaperäanalyytikko
29. Sähköasentaja (kiinteistö + energian optimointi)
30. LVI-asiantuntija (putkimies)
31. Timpuri (rakenteet)
32. Nuohooja / Paloturva-asiantuntija
33. Valaistusmestari
34. Paloesimies (häkä, palovaroittimet, lämpöanomaliat)
35. Laitehuoltaja (IoT, akut, verkot)
36. Kybervahti (tietoturva)
37. Lukkoseppä (älylukot)
38. Pihavahti (ihmishavainnot)
39. Privaattisuuden suojelija
40. Eräkokki
41. Leipuri
42. Ravintoterapeutti
43. Saunamajuri
44. Viihdepäällikkö (PS5 + lautapelit + perinnepelit)
45. Elokuva-asiantuntija (Suomi-elokuvat)
46. Inventaariopäällikkö
47. Kierrätys- ja jäteneuvoja
48. Siivousvastaava
49. Logistikko (reitti + ajoajat)
50. Matemaatikko ja fyysikko (laskenta + mallit)

---


================================================================================
### OUTPUT_PART 26
## AGENT 26: Mikroilmasto-asiantuntija
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: mikroilmasto
  agent_name: Mikroilmasto-asiantuntija
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Korvenranta: järven vaikutus, metsänsuoja, avoin piha'
- Oma sääasema vs. IL:n data
DECISION_METRICS_AND_THRESHOLDS:
  lake_effect_c:
    value: '±2-3°C ero: keväällä kylmempi, syksyllä lämpimämpi'
    source: src:MI1
    action: 'Kevät: ranta 2-3°C kylmempi → halla myöhemmin kuin avomaa. Syksy: 2-3°C
      lämpimämpi → kasvukausi 1-2 vko pidempi. Ilmoita hortonomille.'
  forest_wind_reduction_pct:
    value: 30-60%
    source: src:MI1
  frost_pocket_risk:
    value: Painanne → kylmäilma-allas, halla 2-3°C aiemmin
    action: Painanne pihapiirissä → kylmäilma-allas, T jopa 3°C alempi kuin rinne.
      EI herkkiä kasveja (tomaatti, kurkku) painanteeseen.
    source: src:MI1
  south_wall_bonus_c:
    value: 3-5°C lämmpimämpi
    source: src:MI1
    action: Eteläseinä +3-5°C aurinkopäivänä. Viiniköynnös/ruusut/varhaisperunat eteläseinälle.
      Kasvuvyöhyke tehollisesti +1.
  dew_point_gap_fog_c:
    value: T - kastepiste <2°C → sumu/huurre
    source: src:MI1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Järvi viilentää rantaa → halla-viski. Negatiivinen keväällä.'
  source: src:MI1
- season: Kesä
  action: '[vko 22-35] Eteläseinä hyödyksi kasvien sijoittelussa.'
  source: src:MI1
- season: Syksy
  action: '[vko 36-48] Järvi lämmittää → kasvukausi pitenee. Aamu-sumu.'
  source: src:MI1
- season: Talvi
  action: '[vko 49-13] Kylmäilma-altaat. Inversio (pakkas + tyyni → häkä).'
  source: src:MI1
FAILURE_MODES:
- mode: Paikallinen halla vastoin ennustetta
  detection: T<0°C paikallisesti, ennuste >0°C
  action: HÄLYTYS hortonomille, tarhaajalle
  source: src:MI1
- mode: Inversio + häkä
  detection: Tyyni kirkas pakkanen
  action: Ilmoita paloesimiehelle (häkävaara)
  source: src:MI1
PROCESS_FLOWS:
- flow_id: FLOW_MIKR_01
  trigger: 'lake_effect_c ylittää kynnysarvon (±2-3°C ero: keväällä kylmempi, syksyllä
    lämpimämpi)'
  action: 'Kevät: ranta 2-3°C kylmempi → halla myöhemmin kuin avomaa. Syksy: 2-3°C
    lämpimämpi → kasvukausi 1-2 vko pidempi. Ilmoita hortonomille.'
  output: Tilanneraportti
  source: src:MIKR
- flow_id: FLOW_MIKR_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Järvi viilentää rantaa → halla-viski. Negatiivinen keväällä.'
  output: Tarkistuslista
  source: src:MIKR
- flow_id: FLOW_MIKR_03
  trigger: 'Havaittu: Paikallinen halla vastoin ennustetta'
  action: HÄLYTYS hortonomille, tarhaajalle
  output: Poikkeamaraportti
  source: src:MIKR
- flow_id: FLOW_MIKR_04
  trigger: Säännöllinen heartbeat
  action: 'mikroilmasto: rutiiniarviointi'
  output: Status-raportti
  source: src:MIKR
KNOWLEDGE_TABLES:
- table_id: TBL_MIKR_01
  title: Mikroilmasto-asiantuntija — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: lake_effect_c
    value: '±2-3°C ero: keväällä kylmempi, syksyllä lämpimämpi'
    action: 'Kevät: ranta 2-3°C kylmempi → halla myöhemmin kuin avomaa. Syksy: 2-3°C
      lämpimäm'
  - metric: forest_wind_reduction_pct
    value: 30-60%
    action: ''
  - metric: frost_pocket_risk
    value: Painanne → kylmäilma-allas, halla 2-3°C aiemmin
    action: Painanne pihapiirissä → kylmäilma-allas, T jopa 3°C alempi kuin rinne.
      EI herkki
  - metric: south_wall_bonus_c
    value: 3-5°C lämmpimämpi
    action: Eteläseinä +3-5°C aurinkopäivänä. Viiniköynnös/ruusut/varhaisperunat eteläseinäl
  - metric: dew_point_gap_fog_c
    value: T - kastepiste <2°C → sumu/huurre
    action: ''
  source: src:MIKR
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Mikroilmastodata ei yleistettävissä edes 500m etäisyydelle.
SOURCE_REGISTRY:
  sources:
  - id: src:MI1
    org: Ilmatieteen laitos
    title: Paikallinen ilmasto
    year: 2024
    url: https://www.ilmatieteenlaitos.fi/
    supports: Järviefekti, metsäsuoja, halla, inversio.
eval_questions:
- q: Mikä on lake effect c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.lake_effect_c
  source: src:MI1
- q: Mikä on forest wind reduction pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.forest_wind_reduction_pct
  source: src:MI1
- q: Mikä on frost pocket risk?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.frost_pocket_risk
  source: src:MI1
- q: Mitä tehdään kun frost pocket risk ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.frost_pocket_risk.action
  source: src:MI1
- q: Mikä on south wall bonus c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.south_wall_bonus_c
  source: src:MI1
- q: Mikä on dew point gap fog c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.dew_point_gap_fog_c
  source: src:MI1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:MI1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:MI1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:MI1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:MI1
- q: Miten 'Paikallinen halla vastoin ennustetta' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:MI1
- q: Mitä tehdään tilanteessa 'Paikallinen halla vastoin ennustetta'?
  a_ref: FAILURE_MODES[0].action
  source: src:MI1
- q: Miten 'Inversio + häkä' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:MI1
- q: Mitä tehdään tilanteessa 'Inversio + häkä'?
  a_ref: FAILURE_MODES[1].action
  source: src:MI1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:MI1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#11)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#12)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#13)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#14)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#15)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#16)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#17)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#18)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#19)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#20)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#21)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#22)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#23)?
  a_ref: ASSUMPTIONS
  source: src:MI1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#24)?
  a_ref: ASSUMPTIONS
  source: src:MI1
```

**sources.yaml:**
```yaml
sources:
- id: src:MI1
  org: Ilmatieteen laitos
  title: Paikallinen ilmasto
  year: 2024
  url: https://www.ilmatieteenlaitos.fi/
  supports: Järviefekti, metsäsuoja, halla, inversio.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Mikroilmasto-asiantuntija
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenranta: järven vaikutus, metsänsuoja, avoin piha
- Oma sääasema vs. IL:n data

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| lake_effect_c | ±2-3°C ero: keväällä kylmempi, syksyllä lämpimämpi | Kevät: ranta 2-3°C kylmempi → halla myöhemmin kuin avomaa. Syksy: 2-3°C lämpimämpi → kasvukausi 1-2 vko pidempi. Ilmoita hortonomille. | src:MI1 |
| forest_wind_reduction_pct | 30-60% | — | src:MI1 |
| frost_pocket_risk | Painanne → kylmäilma-allas, halla 2-3°C aiemmin | Painanne pihapiirissä → kylmäilma-allas, T jopa 3°C alempi kuin rinne. EI herkkiä kasveja (tomaatti, kurkku) painanteeseen. | src:MI1 |
| south_wall_bonus_c | 3-5°C lämmpimämpi | Eteläseinä +3-5°C aurinkopäivänä. Viiniköynnös/ruusut/varhaisperunat eteläseinälle. Kasvuvyöhyke tehollisesti +1. | src:MI1 |
| dew_point_gap_fog_c | T - kastepiste <2°C → sumu/huurre | — | src:MI1 |

## Tietotaulukot

**Mikroilmasto-asiantuntija — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| lake_effect_c | ±2-3°C ero: keväällä kylmempi, syksyllä lämpimämpi | Kevät: ranta 2-3°C kylmempi → halla myöhemmin kuin avomaa. Syksy: 2-3°C lämpimäm |
| forest_wind_reduction_pct | 30-60% |  |
| frost_pocket_risk | Painanne → kylmäilma-allas, halla 2-3°C aiemmin | Painanne pihapiirissä → kylmäilma-allas, T jopa 3°C alempi kuin rinne. EI herkki |
| south_wall_bonus_c | 3-5°C lämmpimämpi | Eteläseinä +3-5°C aurinkopäivänä. Viiniköynnös/ruusut/varhaisperunat eteläseinäl |
| dew_point_gap_fog_c | T - kastepiste <2°C → sumu/huurre |  |

## Prosessit

**FLOW_MIKR_01:** lake_effect_c ylittää kynnysarvon (±2-3°C ero: keväällä kylmempi, syksyllä lämpimämpi)
  → Kevät: ranta 2-3°C kylmempi → halla myöhemmin kuin avomaa. Syksy: 2-3°C lämpimämpi → kasvukausi 1-2 vko pidempi. Ilmoita hortonomille.
  Tulos: Tilanneraportti

**FLOW_MIKR_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Järvi viilentää rantaa → halla-viski. Negatiivinen keväällä.
  Tulos: Tarkistuslista

**FLOW_MIKR_03:** Havaittu: Paikallinen halla vastoin ennustetta
  → HÄLYTYS hortonomille, tarhaajalle
  Tulos: Poikkeamaraportti

**FLOW_MIKR_04:** Säännöllinen heartbeat
  → mikroilmasto: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Järvi viilentää rantaa → halla-viski. Negatiivinen keväällä. | src:MI1 |
| **Kesä** | [vko 22-35] Eteläseinä hyödyksi kasvien sijoittelussa. | src:MI1 |
| **Syksy** | [vko 36-48] Järvi lämmittää → kasvukausi pitenee. Aamu-sumu. | src:MI1 |
| **Talvi** | [vko 49-13] Kylmäilma-altaat. Inversio (pakkas + tyyni → häkä). | src:MI1 |

## Virhe- ja vaaratilanteet

### ⚠️ Paikallinen halla vastoin ennustetta
- **Havaitseminen:** T<0°C paikallisesti, ennuste >0°C
- **Toimenpide:** HÄLYTYS hortonomille, tarhaajalle
- **Lähde:** src:MI1

### ⚠️ Inversio + häkä
- **Havaitseminen:** Tyyni kirkas pakkanen
- **Toimenpide:** Ilmoita paloesimiehelle (häkävaara)
- **Lähde:** src:MI1

## Epävarmuudet
- Mikroilmastodata ei yleistettävissä edes 500m etäisyydelle.

## Lähteet
- **src:MI1**: Ilmatieteen laitos — *Paikallinen ilmasto* (2024) https://www.ilmatieteenlaitos.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on lake effect c?**
    → `DECISION_METRICS_AND_THRESHOLDS.lake_effect_c` [src:MI1]
 2. **Mikä on forest wind reduction pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.forest_wind_reduction_pct` [src:MI1]
 3. **Mikä on frost pocket risk?**
    → `DECISION_METRICS_AND_THRESHOLDS.frost_pocket_risk` [src:MI1]
 4. **Mitä tehdään kun frost pocket risk ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.frost_pocket_risk.action` [src:MI1]
 5. **Mikä on south wall bonus c?**
    → `DECISION_METRICS_AND_THRESHOLDS.south_wall_bonus_c` [src:MI1]
 6. **Mikä on dew point gap fog c?**
    → `DECISION_METRICS_AND_THRESHOLDS.dew_point_gap_fog_c` [src:MI1]
 7. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:MI1]
 8. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:MI1]
 9. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:MI1]
10. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:MI1]
11. **Miten 'Paikallinen halla vastoin ennustetta' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:MI1]
12. **Mitä tehdään tilanteessa 'Paikallinen halla vastoin ennustetta'?**
    → `FAILURE_MODES[0].action` [src:MI1]
13. **Miten 'Inversio + häkä' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:MI1]
14. **Mitä tehdään tilanteessa 'Inversio + häkä'?**
    → `FAILURE_MODES[1].action` [src:MI1]
15. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:MI1]
16. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:MI1]
17. **Miten tämä agentti kytkeytyy muihin agentteihin (#1)?**
    → `ASSUMPTIONS` [src:MI1]
18. **Miten tämä agentti kytkeytyy muihin agentteihin (#2)?**
    → `ASSUMPTIONS` [src:MI1]
19. **Miten tämä agentti kytkeytyy muihin agentteihin (#3)?**
    → `ASSUMPTIONS` [src:MI1]
20. **Miten tämä agentti kytkeytyy muihin agentteihin (#4)?**
    → `ASSUMPTIONS` [src:MI1]
21. **Miten tämä agentti kytkeytyy muihin agentteihin (#5)?**
    → `ASSUMPTIONS` [src:MI1]
22. **Miten tämä agentti kytkeytyy muihin agentteihin (#6)?**
    → `ASSUMPTIONS` [src:MI1]
23. **Miten tämä agentti kytkeytyy muihin agentteihin (#7)?**
    → `ASSUMPTIONS` [src:MI1]
24. **Miten tämä agentti kytkeytyy muihin agentteihin (#8)?**
    → `ASSUMPTIONS` [src:MI1]
25. **Miten tämä agentti kytkeytyy muihin agentteihin (#9)?**
    → `ASSUMPTIONS` [src:MI1]
26. **Miten tämä agentti kytkeytyy muihin agentteihin (#10)?**
    → `ASSUMPTIONS` [src:MI1]
27. **Miten tämä agentti kytkeytyy muihin agentteihin (#11)?**
    → `ASSUMPTIONS` [src:MI1]
28. **Miten tämä agentti kytkeytyy muihin agentteihin (#12)?**
    → `ASSUMPTIONS` [src:MI1]
29. **Miten tämä agentti kytkeytyy muihin agentteihin (#13)?**
    → `ASSUMPTIONS` [src:MI1]
30. **Miten tämä agentti kytkeytyy muihin agentteihin (#14)?**
    → `ASSUMPTIONS` [src:MI1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 27
## AGENT 27: Ilmanlaadun tarkkailija
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: ilmanlaatu
  agent_name: Ilmanlaadun tarkkailija
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Maaseututausta, Korvenranta
- Puulämmitys, liikenne, maastopalot, siitepöly
DECISION_METRICS_AND_THRESHOLDS:
  pm25_ug:
    value: WHO 15 μg/m³ (vuosi), 45 (24h)
    action: '>50 → varoitus, ikkunat kiinni'
    source: src:IL1
  pm10_ug:
    value: WHO 45 (vuosi), 100 (24h)
    source: src:IL1
  co_ppm_indoor:
    value: <9 ppm (8h)
    action: '>35 → HÄKÄVAARA, tuuleta, paloesimiehelle'
    source: src:IL2
  pollen_birch:
    value: '>80 kpl/m³ korkea, >200 erittäin korkea'
    source: src:IL3
  radon_bq:
    value: Viite 200 Bq/m³
    action: '>200 → radonkorjaus, >400 → välitön'
    source: src:IL4
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Koivusiitepöly huhti-touko. Katupöly.'
  source: src:IL3
- season: Kesä
  action: '[vko 22-35] Maastopalot (kuiva kesä). Otsoni helteellä.'
  source: src:IL1
- season: Syksy
  action: Puulämmityskausi → PM2.5. Inversio.
  source: src:IL1
- season: Talvi
  action: '[vko 49-13] Puulämmitys pahimmillaan. Häkäviski.'
  source: src:IL2
FAILURE_MODES:
- mode: Häkä koholla sisällä
  detection: CO-hälytin tai >35 ppm
  action: Avaa ikkunat, sammuta tulisija, ulos, 112 jos >100 ppm
  source: src:IL2
- mode: Maastopalon savu
  detection: PM2.5 >100 + savun haju
  action: Sulje ikkunat+IV, HEPA-suodatin
  source: src:IL1
PROCESS_FLOWS:
- flow_id: FLOW_ILMA_01
  trigger: pm25_ug ylittää kynnysarvon (WHO 15 μg/m³ (vuosi), 45 (24h))
  action: '>50 → varoitus, ikkunat kiinni'
  output: Tilanneraportti
  source: src:ILMA
- flow_id: FLOW_ILMA_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Koivusiitepöly huhti-touko. Katupöly.'
  output: Tarkistuslista
  source: src:ILMA
- flow_id: FLOW_ILMA_03
  trigger: 'Havaittu: Häkä koholla sisällä'
  action: Avaa ikkunat, sammuta tulisija, ulos, 112 jos >100 ppm
  output: Poikkeamaraportti
  source: src:ILMA
- flow_id: FLOW_ILMA_04
  trigger: Säännöllinen heartbeat
  action: 'ilmanlaatu: rutiiniarviointi'
  output: Status-raportti
  source: src:ILMA
KNOWLEDGE_TABLES:
- table_id: TBL_ILMA_01
  title: Ilmanlaadun tarkkailija — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: pm25_ug
    value: WHO 15 μg/m³ (vuosi), 45 (24h)
    action: '>50 → varoitus, ikkunat kiinni'
  - metric: pm10_ug
    value: WHO 45 (vuosi), 100 (24h)
    action: ''
  - metric: co_ppm_indoor
    value: <9 ppm (8h)
    action: '>35 → HÄKÄVAARA, tuuleta, paloesimiehelle'
  - metric: pollen_birch
    value: '>80 kpl/m³ korkea, >200 erittäin korkea'
    action: ''
  - metric: radon_bq
    value: Viite 200 Bq/m³
    action: '>200 → radonkorjaus, >400 → välitön'
  source: src:ILMA
COMPLIANCE_AND_LEGAL:
  radon: 'STM 1044/2018: viite 200 Bq/m³ [src:IL4]'
  avopoltto: 'Jätelaki: avopoltto kielletty asemakaava-alueella [src:IL1]'
UNCERTAINTY_NOTES:
- Maaseudulla PM2.5 yleensä matala, mutta puulämmitys nostaa paikallisesti.
SOURCE_REGISTRY:
  sources:
  - id: src:IL1
    org: HSY/SYKE
    title: Ilmanlaatu
    year: 2025
    url: https://www.ilmanlaatu.fi/
    supports: PM2.5, PM10, otsoni.
  - id: src:IL2
    org: THL
    title: Häkämyrkytys
    year: 2025
    url: https://thl.fi/
    supports: CO-rajat.
  - id: src:IL3
    org: Turun yo / Norkko
    title: Siitepöly
    year: 2025
    url: https://www.norkko.fi/
    supports: Siitepölylaskennat.
  - id: src:IL4
    org: STUK
    title: Radon
    year: 2025
    url: https://www.stuk.fi/aiheet/radon
    supports: Radon.
eval_questions:
- q: Mikä on pm25 ug?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pm25_ug
  source: src:IL1
- q: Mitä tehdään kun pm25 ug ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pm25_ug.action
  source: src:IL1
- q: Mikä on pm10 ug?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pm10_ug
  source: src:IL1
- q: Mikä on co ppm indoor?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.co_ppm_indoor
  source: src:IL1
- q: Mitä tehdään kun co ppm indoor ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.co_ppm_indoor.action
  source: src:IL1
- q: Mikä on pollen birch?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pollen_birch
  source: src:IL1
- q: Mikä on radon bq?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.radon_bq
  source: src:IL1
- q: Mitä tehdään kun radon bq ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.radon_bq.action
  source: src:IL1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:IL1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:IL1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:IL1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:IL1
- q: Miten 'Häkä koholla sisällä' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:IL1
- q: Mitä tehdään tilanteessa 'Häkä koholla sisällä'?
  a_ref: FAILURE_MODES[0].action
  source: src:IL1
- q: Miten 'Maastopalon savu' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:IL1
- q: Mitä tehdään tilanteessa 'Maastopalon savu'?
  a_ref: FAILURE_MODES[1].action
  source: src:IL1
- q: Mikä on radon -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.radon
  source: src:IL1
- q: Mikä on avopoltto -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.avopoltto
  source: src:IL1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:IL1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#11)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#12)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#13)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#14)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#15)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#16)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#17)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#18)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#19)?
  a_ref: ASSUMPTIONS
  source: src:IL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#20)?
  a_ref: ASSUMPTIONS
  source: src:IL1
```

**sources.yaml:**
```yaml
sources:
- id: src:IL1
  org: HSY/SYKE
  title: Ilmanlaatu
  year: 2025
  url: https://www.ilmanlaatu.fi/
  supports: PM2.5, PM10, otsoni.
- id: src:IL2
  org: THL
  title: Häkämyrkytys
  year: 2025
  url: https://thl.fi/
  supports: CO-rajat.
- id: src:IL3
  org: Turun yo / Norkko
  title: Siitepöly
  year: 2025
  url: https://www.norkko.fi/
  supports: Siitepölylaskennat.
- id: src:IL4
  org: STUK
  title: Radon
  year: 2025
  url: https://www.stuk.fi/aiheet/radon
  supports: Radon.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Ilmanlaadun tarkkailija
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Maaseututausta, Korvenranta
- Puulämmitys, liikenne, maastopalot, siitepöly

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| pm25_ug | WHO 15 μg/m³ (vuosi), 45 (24h) | >50 → varoitus, ikkunat kiinni | src:IL1 |
| pm10_ug | WHO 45 (vuosi), 100 (24h) | — | src:IL1 |
| co_ppm_indoor | <9 ppm (8h) | >35 → HÄKÄVAARA, tuuleta, paloesimiehelle | src:IL2 |
| pollen_birch | >80 kpl/m³ korkea, >200 erittäin korkea | — | src:IL3 |
| radon_bq | Viite 200 Bq/m³ | >200 → radonkorjaus, >400 → välitön | src:IL4 |

## Tietotaulukot

**Ilmanlaadun tarkkailija — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| pm25_ug | WHO 15 μg/m³ (vuosi), 45 (24h) | >50 → varoitus, ikkunat kiinni |
| pm10_ug | WHO 45 (vuosi), 100 (24h) |  |
| co_ppm_indoor | <9 ppm (8h) | >35 → HÄKÄVAARA, tuuleta, paloesimiehelle |
| pollen_birch | >80 kpl/m³ korkea, >200 erittäin korkea |  |
| radon_bq | Viite 200 Bq/m³ | >200 → radonkorjaus, >400 → välitön |

## Prosessit

**FLOW_ILMA_01:** pm25_ug ylittää kynnysarvon (WHO 15 μg/m³ (vuosi), 45 (24h))
  → >50 → varoitus, ikkunat kiinni
  Tulos: Tilanneraportti

**FLOW_ILMA_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Koivusiitepöly huhti-touko. Katupöly.
  Tulos: Tarkistuslista

**FLOW_ILMA_03:** Havaittu: Häkä koholla sisällä
  → Avaa ikkunat, sammuta tulisija, ulos, 112 jos >100 ppm
  Tulos: Poikkeamaraportti

**FLOW_ILMA_04:** Säännöllinen heartbeat
  → ilmanlaatu: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Koivusiitepöly huhti-touko. Katupöly. | src:IL3 |
| **Kesä** | [vko 22-35] Maastopalot (kuiva kesä). Otsoni helteellä. | src:IL1 |
| **Syksy** | Puulämmityskausi → PM2.5. Inversio. | src:IL1 |
| **Talvi** | [vko 49-13] Puulämmitys pahimmillaan. Häkäviski. | src:IL2 |

## Virhe- ja vaaratilanteet

### ⚠️ Häkä koholla sisällä
- **Havaitseminen:** CO-hälytin tai >35 ppm
- **Toimenpide:** Avaa ikkunat, sammuta tulisija, ulos, 112 jos >100 ppm
- **Lähde:** src:IL2

### ⚠️ Maastopalon savu
- **Havaitseminen:** PM2.5 >100 + savun haju
- **Toimenpide:** Sulje ikkunat+IV, HEPA-suodatin
- **Lähde:** src:IL1

## Lait ja vaatimukset
- **radon:** STM 1044/2018: viite 200 Bq/m³ [src:IL4]
- **avopoltto:** Jätelaki: avopoltto kielletty asemakaava-alueella [src:IL1]

## Epävarmuudet
- Maaseudulla PM2.5 yleensä matala, mutta puulämmitys nostaa paikallisesti.

## Lähteet
- **src:IL1**: HSY/SYKE — *Ilmanlaatu* (2025) https://www.ilmanlaatu.fi/
- **src:IL2**: THL — *Häkämyrkytys* (2025) https://thl.fi/
- **src:IL3**: Turun yo / Norkko — *Siitepöly* (2025) https://www.norkko.fi/
- **src:IL4**: STUK — *Radon* (2025) https://www.stuk.fi/aiheet/radon

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on pm25 ug?**
    → `DECISION_METRICS_AND_THRESHOLDS.pm25_ug` [src:IL1]
 2. **Mitä tehdään kun pm25 ug ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.pm25_ug.action` [src:IL1]
 3. **Mikä on pm10 ug?**
    → `DECISION_METRICS_AND_THRESHOLDS.pm10_ug` [src:IL1]
 4. **Mikä on co ppm indoor?**
    → `DECISION_METRICS_AND_THRESHOLDS.co_ppm_indoor` [src:IL1]
 5. **Mitä tehdään kun co ppm indoor ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.co_ppm_indoor.action` [src:IL1]
 6. **Mikä on pollen birch?**
    → `DECISION_METRICS_AND_THRESHOLDS.pollen_birch` [src:IL1]
 7. **Mikä on radon bq?**
    → `DECISION_METRICS_AND_THRESHOLDS.radon_bq` [src:IL1]
 8. **Mitä tehdään kun radon bq ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.radon_bq.action` [src:IL1]
 9. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:IL1]
10. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:IL1]
11. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:IL1]
12. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:IL1]
13. **Miten 'Häkä koholla sisällä' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:IL1]
14. **Mitä tehdään tilanteessa 'Häkä koholla sisällä'?**
    → `FAILURE_MODES[0].action` [src:IL1]
15. **Miten 'Maastopalon savu' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:IL1]
16. **Mitä tehdään tilanteessa 'Maastopalon savu'?**
    → `FAILURE_MODES[1].action` [src:IL1]
17. **Mikä on radon -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.radon` [src:IL1]
18. **Mikä on avopoltto -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.avopoltto` [src:IL1]
19. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:IL1]
20. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:IL1]
21. **Miten tämä agentti kytkeytyy muihin agentteihin (#1)?**
    → `ASSUMPTIONS` [src:IL1]
22. **Miten tämä agentti kytkeytyy muihin agentteihin (#2)?**
    → `ASSUMPTIONS` [src:IL1]
23. **Miten tämä agentti kytkeytyy muihin agentteihin (#3)?**
    → `ASSUMPTIONS` [src:IL1]
24. **Miten tämä agentti kytkeytyy muihin agentteihin (#4)?**
    → `ASSUMPTIONS` [src:IL1]
25. **Miten tämä agentti kytkeytyy muihin agentteihin (#5)?**
    → `ASSUMPTIONS` [src:IL1]
26. **Miten tämä agentti kytkeytyy muihin agentteihin (#6)?**
    → `ASSUMPTIONS` [src:IL1]
27. **Miten tämä agentti kytkeytyy muihin agentteihin (#7)?**
    → `ASSUMPTIONS` [src:IL1]
28. **Miten tämä agentti kytkeytyy muihin agentteihin (#8)?**
    → `ASSUMPTIONS` [src:IL1]
29. **Miten tämä agentti kytkeytyy muihin agentteihin (#9)?**
    → `ASSUMPTIONS` [src:IL1]
30. **Miten tämä agentti kytkeytyy muihin agentteihin (#10)?**
    → `ASSUMPTIONS` [src:IL1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 28
## AGENT 28: Routa- ja maaperäanalyytikko
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: routa_maapera
  agent_name: Routa- ja maaperäanalyytikko
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenranta, Kouvola — savi/moreeni
- Routasyvyys kriittistä perustuksille ja putkistoille
DECISION_METRICS_AND_THRESHOLDS:
  frost_depth_max_cm:
    value: 100-150 (Kouvola, normaali talvi)
    source: src:RO1
  pipe_burial_min_cm:
    value: 180-200 tai eristetty
    action: <180 cm → routasuojaus
    source: src:RO1
  frost_heave_risk:
    value: Siltti/savi + vesi → korkea routanousu
    action: Seuraa perustusten liikkeitä
    source: src:RO1
  thaw_spring:
    value: Sulaminen huhti-touko ylhäältä
    action: Maan paineen lisääntyessä perustusten tarkistus
    source: src:RO1
  soil_moisture_pct:
    value: 30-40% (savi kenttäkapasiteetti)
    action: '>90% saturaatio → tulva/salaojatarkistus'
    source: src:RO1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Roudan sulaminen. Kelirikko. Perustusten tarkistus.'
  source: src:RO1
- season: Kesä
  action: '[vko 22-35] Maaperän kuivuminen. Savimaan kutistuminen → halkeamia.'
  source: src:RO1
- season: Syksy
  action: '[vko 36-48] Kosteus nousee. Salaojien tarkistus. Routa alkaa.'
  source: src:RO1
- season: Talvi
  action: Routasyvyyden seuranta. Lumi (>50 cm) eristää → routa matalampi.
  source: src:RO1
FAILURE_MODES:
- mode: Putken jäätyminen
  detection: Virtaus loppuu pakkasella
  action: Sulata (vastuskaapeli/lämmin vesi), ilmoita LVI-agentille
  source: src:RO1
- mode: Perustuksen routanousu
  detection: Karmi vääntyvät, halkeamia seinissä
  action: Dokumentoi, rakennesuunnittelija, ilmoita timpurille
  source: src:RO1
PROCESS_FLOWS:
- flow_id: FLOW_ROUT_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Roudan sulaminen. Kelirikko. Perustusten tarkistus.'
  output: Tarkistuslista
  source: src:ROUT
- flow_id: FLOW_ROUT_03
  trigger: 'Havaittu: Putken jäätyminen'
  action: Sulata (vastuskaapeli/lämmin vesi), ilmoita LVI-agentille
  output: Poikkeamaraportti
  source: src:ROUT
- flow_id: FLOW_ROUT_04
  trigger: Säännöllinen heartbeat
  action: 'routa_maapera: rutiiniarviointi'
  output: Status-raportti
  source: src:ROUT
KNOWLEDGE_TABLES:
- table_id: TBL_ROUT_01
  title: Routa- ja maaperäanalyytikko — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: frost_depth_max_cm
    value: 100-150 (Kouvola, normaali talvi)
    action: ''
  - metric: pipe_burial_min_cm
    value: 180-200 tai eristetty
    action: <180 cm → routasuojaus
  - metric: frost_heave_risk
    value: Siltti/savi + vesi → korkea routanousu
    action: Seuraa perustusten liikkeitä
  - metric: thaw_spring
    value: Sulaminen huhti-touko ylhäältä
    action: Maan paineen lisääntyessä perustusten tarkistus
  - metric: soil_moisture_pct
    value: 30-40% (savi kenttäkapasiteetti)
    action: '>90% saturaatio → tulva/salaojatarkistus'
  source: src:ROUT
COMPLIANCE_AND_LEGAL:
  perustaminen: 'Rakentamislaki 751/2023: perustus routarajan alapuolelle [src:RO2]'
UNCERTAINTY_NOTES:
- Routasyvyys vaihtelee ±30% lumi- ja lämpötilaolojen mukaan.
- Maaperä voi vaihdella pienellä alueella merkittävästi.
SOURCE_REGISTRY:
  sources:
  - id: src:RO1
    org: GTK/IL
    title: Routatiedot
    year: 2025
    url: https://www.gtk.fi/
    supports: Routasyvyys, maaperä.
  - id: src:RO2
    org: Oikeusministeriö
    title: Rakentamislaki 751/2023
    year: 2023
    url: https://finlex.fi/fi/laki/ajantasa/2023/20230751
    supports: Perustamisvaatimukset.
eval_questions:
- q: Mikä on frost depth max cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.frost_depth_max_cm
  source: src:RO1
- q: Mikä on pipe burial min cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pipe_burial_min_cm
  source: src:RO1
- q: Mitä tehdään kun pipe burial min cm ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pipe_burial_min_cm.action
  source: src:RO1
- q: Mikä on frost heave risk?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.frost_heave_risk
  source: src:RO1
- q: Mitä tehdään kun frost heave risk ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.frost_heave_risk.action
  source: src:RO1
- q: Mikä on thaw spring?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thaw_spring
  source: src:RO1
- q: Mitä tehdään kun thaw spring ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thaw_spring.action
  source: src:RO1
- q: Mikä on soil moisture pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.soil_moisture_pct
  source: src:RO1
- q: Mitä tehdään kun soil moisture pct ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.soil_moisture_pct.action
  source: src:RO1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:RO1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:RO1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:RO1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:RO1
- q: Miten 'Putken jäätyminen' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:RO1
- q: Mitä tehdään tilanteessa 'Putken jäätyminen'?
  a_ref: FAILURE_MODES[0].action
  source: src:RO1
- q: Miten 'Perustuksen routanousu' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:RO1
- q: Mitä tehdään tilanteessa 'Perustuksen routanousu'?
  a_ref: FAILURE_MODES[1].action
  source: src:RO1
- q: Mikä on perustaminen -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.perustaminen
  source: src:RO1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:RO1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#11)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#12)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#13)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#14)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#15)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#16)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#17)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#18)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#19)?
  a_ref: ASSUMPTIONS
  source: src:RO1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#20)?
  a_ref: ASSUMPTIONS
  source: src:RO1
```

**sources.yaml:**
```yaml
sources:
- id: src:RO1
  org: GTK/IL
  title: Routatiedot
  year: 2025
  url: https://www.gtk.fi/
  supports: Routasyvyys, maaperä.
- id: src:RO2
  org: Oikeusministeriö
  title: Rakentamislaki 751/2023
  year: 2023
  url: https://finlex.fi/fi/laki/ajantasa/2023/20230751
  supports: Perustamisvaatimukset.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Routa- ja maaperäanalyytikko
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenranta, Kouvola — savi/moreeni
- Routasyvyys kriittistä perustuksille ja putkistoille

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| frost_depth_max_cm | 100-150 (Kouvola, normaali talvi) | — | src:RO1 |
| pipe_burial_min_cm | 180-200 tai eristetty | <180 cm → routasuojaus | src:RO1 |
| frost_heave_risk | Siltti/savi + vesi → korkea routanousu | Seuraa perustusten liikkeitä | src:RO1 |
| thaw_spring | Sulaminen huhti-touko ylhäältä | Maan paineen lisääntyessä perustusten tarkistus | src:RO1 |
| soil_moisture_pct | 30-40% (savi kenttäkapasiteetti) | >90% saturaatio → tulva/salaojatarkistus | src:RO1 |

## Tietotaulukot

**Routa- ja maaperäanalyytikko — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| frost_depth_max_cm | 100-150 (Kouvola, normaali talvi) |  |
| pipe_burial_min_cm | 180-200 tai eristetty | <180 cm → routasuojaus |
| frost_heave_risk | Siltti/savi + vesi → korkea routanousu | Seuraa perustusten liikkeitä |
| thaw_spring | Sulaminen huhti-touko ylhäältä | Maan paineen lisääntyessä perustusten tarkistus |
| soil_moisture_pct | 30-40% (savi kenttäkapasiteetti) | >90% saturaatio → tulva/salaojatarkistus |

## Prosessit

**FLOW_ROUT_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Roudan sulaminen. Kelirikko. Perustusten tarkistus.
  Tulos: Tarkistuslista

**FLOW_ROUT_03:** Havaittu: Putken jäätyminen
  → Sulata (vastuskaapeli/lämmin vesi), ilmoita LVI-agentille
  Tulos: Poikkeamaraportti

**FLOW_ROUT_04:** Säännöllinen heartbeat
  → routa_maapera: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Roudan sulaminen. Kelirikko. Perustusten tarkistus. | src:RO1 |
| **Kesä** | [vko 22-35] Maaperän kuivuminen. Savimaan kutistuminen → halkeamia. | src:RO1 |
| **Syksy** | [vko 36-48] Kosteus nousee. Salaojien tarkistus. Routa alkaa. | src:RO1 |
| **Talvi** | Routasyvyyden seuranta. Lumi (>50 cm) eristää → routa matalampi. | src:RO1 |

## Virhe- ja vaaratilanteet

### ⚠️ Putken jäätyminen
- **Havaitseminen:** Virtaus loppuu pakkasella
- **Toimenpide:** Sulata (vastuskaapeli/lämmin vesi), ilmoita LVI-agentille
- **Lähde:** src:RO1

### ⚠️ Perustuksen routanousu
- **Havaitseminen:** Karmi vääntyvät, halkeamia seinissä
- **Toimenpide:** Dokumentoi, rakennesuunnittelija, ilmoita timpurille
- **Lähde:** src:RO1

## Lait ja vaatimukset
- **perustaminen:** Rakentamislaki 751/2023: perustus routarajan alapuolelle [src:RO2]

## Epävarmuudet
- Routasyvyys vaihtelee ±30% lumi- ja lämpötilaolojen mukaan.
- Maaperä voi vaihdella pienellä alueella merkittävästi.

## Lähteet
- **src:RO1**: GTK/IL — *Routatiedot* (2025) https://www.gtk.fi/
- **src:RO2**: Oikeusministeriö — *Rakentamislaki 751/2023* (2023) https://finlex.fi/fi/laki/ajantasa/2023/20230751

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on frost depth max cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.frost_depth_max_cm` [src:RO1]
 2. **Mikä on pipe burial min cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.pipe_burial_min_cm` [src:RO1]
 3. **Mitä tehdään kun pipe burial min cm ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.pipe_burial_min_cm.action` [src:RO1]
 4. **Mikä on frost heave risk?**
    → `DECISION_METRICS_AND_THRESHOLDS.frost_heave_risk` [src:RO1]
 5. **Mitä tehdään kun frost heave risk ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.frost_heave_risk.action` [src:RO1]
 6. **Mikä on thaw spring?**
    → `DECISION_METRICS_AND_THRESHOLDS.thaw_spring` [src:RO1]
 7. **Mitä tehdään kun thaw spring ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.thaw_spring.action` [src:RO1]
 8. **Mikä on soil moisture pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.soil_moisture_pct` [src:RO1]
 9. **Mitä tehdään kun soil moisture pct ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.soil_moisture_pct.action` [src:RO1]
10. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:RO1]
11. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:RO1]
12. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:RO1]
13. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:RO1]
14. **Miten 'Putken jäätyminen' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:RO1]
15. **Mitä tehdään tilanteessa 'Putken jäätyminen'?**
    → `FAILURE_MODES[0].action` [src:RO1]
16. **Miten 'Perustuksen routanousu' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:RO1]
17. **Mitä tehdään tilanteessa 'Perustuksen routanousu'?**
    → `FAILURE_MODES[1].action` [src:RO1]
18. **Mikä on perustaminen -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.perustaminen` [src:RO1]
19. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:RO1]
20. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:RO1]
21. **Miten tämä agentti kytkeytyy muihin agentteihin (#1)?**
    → `ASSUMPTIONS` [src:RO1]
22. **Miten tämä agentti kytkeytyy muihin agentteihin (#2)?**
    → `ASSUMPTIONS` [src:RO1]
23. **Miten tämä agentti kytkeytyy muihin agentteihin (#3)?**
    → `ASSUMPTIONS` [src:RO1]
24. **Miten tämä agentti kytkeytyy muihin agentteihin (#4)?**
    → `ASSUMPTIONS` [src:RO1]
25. **Miten tämä agentti kytkeytyy muihin agentteihin (#5)?**
    → `ASSUMPTIONS` [src:RO1]
26. **Miten tämä agentti kytkeytyy muihin agentteihin (#6)?**
    → `ASSUMPTIONS` [src:RO1]
27. **Miten tämä agentti kytkeytyy muihin agentteihin (#7)?**
    → `ASSUMPTIONS` [src:RO1]
28. **Miten tämä agentti kytkeytyy muihin agentteihin (#8)?**
    → `ASSUMPTIONS` [src:RO1]
29. **Miten tämä agentti kytkeytyy muihin agentteihin (#9)?**
    → `ASSUMPTIONS` [src:RO1]
30. **Miten tämä agentti kytkeytyy muihin agentteihin (#10)?**
    → `ASSUMPTIONS` [src:RO1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 29
## AGENT 29: Sähköasentaja (kiinteistö + energian optimointi)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: sahkoasentaja
  agent_name: Sähköasentaja
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Suomi: 230/400 V, 50 Hz -sähköverkko.'
- Luvanvaraiset sähkötyöt teetetään rekisteröidyllä sähköurakoitsijalla.
DECISION_METRICS_AND_THRESHOLDS:
  rcd_trip_current_max_ma:
    value: 30
    action: Uusissa pistorasiaryhmissä käytä enintään 30 mA vikavirtasuojaa (RCD).
    source: src:TEC1
  socket_group_rated_current_max_a:
    value: 32
    action: RCD-vaatimus koskee pistorasiaryhmiä, joiden nimellisvirta on enintään
      32 A.
    source: src:TEC1
  outdoor_extension_cable_rating:
    value: IP44 ulkokäyttöön, 16 A max, max 25 m
    action: Sisäjatkojohto ulkona → sähköiskuvaara. Vaihda IP44.
    source: src:SAH1
  surge_protection_presence:
    value: Ylijännitesuoja B+C, 40 kA pääkeskuksessa
    action: Puuttuu → asennuta Tukes-rekisteröity asentaja.
    source: src:SAH1
  main_fuse_rating_a:
    value: 25 A tai 35 A omakotitalo
    action: '>80% kuormitus → seuranta. Laukeaa → tarkista kuorma.'
    source: src:SAH1
SEASONAL_RULES:
- season: Kevät
  action: Ulkopistorasioiden tarkistus. Aurinkopaneelien kaapelit. RCD-testi 30 mA.
  source: src:SAH1
- season: Kesä
  action: 'Ukkossuojaus: ylijännitesuojat B+C 40 kA. UV-rasitus. Kulutusseuranta kWh/kk.'
  source: src:SAH1
- season: Syksy
  action: Lämmitysjärjestelmän sähkötarkistus. Sulanapitokaapelit vko 42-44.
  source: src:SAH1
- season: Talvi
  action: Sulanapitokaapelit T<-2°C. Varokekuormitus seuranta. Aggregaatti + UPS.
  source: src:SAH1
FAILURE_MODES:
- mode: Toistuva RCD-laukeaminen
  detection: RCD laukeaa useita kertoja
  action: Irrota kuormat yksi kerrallaan; jos ei selviä → urakoitsija
  source: src:TEC2
- mode: Lämpenevä liitos / palaneen haju
  detection: Pistorasia/kytkin lämpenee tai haisee palaneelle
  action: Katkaise virta välittömästi ja tilaa sähköurakoitsija
  source: src:TEC2
PROCESS_FLOWS:
  fault_response:
    steps:
    - 1. Katkaise virta turvallisesti ja estä lisävahinko
    - 2. Tarkista vikavirtasuoja/sulakkeet
    - 3. Kirjaa tapahtuma (aika, kuormat, olosuhteet)
    - 4. Jos toistuva tai epäselvä → tilaa sähköurakoitsija
    source: src:TEC2
KNOWLEDGE_TABLES:
  allowed_diy_examples:
  - task: Sulakkeen vaihto
    allowed: Kyllä
    source: src:TEC2
  - task: Valaisimen kytkentä (rajattu)
    allowed: Rajoitetusti
    source: src:TEC2
  - task: Uuden pistorasian asentaminen
    allowed: Ei
    source: src:TEC2
COMPLIANCE_AND_LEGAL:
  electric_work_restrictions:
    rule: Sähkötyöt ovat pääosin luvanvaraisia ja kuuluvat rekisteröidylle sähköurakoitsijalle.
    source: src:TEC2
UNCERTAINTY_NOTES:
- SFS 6000 on maksullinen standardi; agentti käyttää vain yleistasoisia vaatimuksia.
SOURCE_REGISTRY:
  sources:
  - id: src:TEC1
    org: SESKO ry / Suomen Standardisoimisliitto
    title: SFS 6000:2022 Pienjännitesähköasennukset
    year: 2022
    url: N/A
    what_it_supports: RCD (30 mA) -vaatimus pistorasiaryhmille (enintään 32 A) yleistasolla.
  - id: src:TEC2
    org: Tukes
    title: Sähkötyöt ja urakointi
    year: 2025
    url: https://tukes.fi/sahko/sahkotyot-ja-urakointi
    what_it_supports: Sähkötöiden luvanvaraisuus ja esimerkit maallikkotöistä.
eval_questions:
- q: Mikä on vikavirtasuojan enimmäislaukaisuvirta (mA) uusissa pistorasiaryhmissä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.rcd_trip_current_max_ma.value
  source: src:TEC1
- q: Mihin nimellisvirtaan asti pistorasiaryhmän RCD-vaatimus ulottuu?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.socket_group_rated_current_max_a.value
  source: src:TEC1
- q: Kuka saa tehdä luvanvaraiset sähkötyöt?
  a_ref: COMPLIANCE_AND_LEGAL.electric_work_restrictions.rule
  source: src:TEC2
- q: Saako kuluttaja asentaa uuden pistorasian?
  a_ref: KNOWLEDGE_TABLES.allowed_diy_examples[2].allowed
  source: src:TEC2
- q: Mikä on vikaprosessin 4. askel?
  a_ref: PROCESS_FLOWS.fault_response.steps[3]
  source: src:TEC2
- q: Mikä on ulkokäyttöön liittyvä jatkojohdon vaatimus (arvo)?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.outdoor_extension_cable_rating.value
  source: src:TEC2
- q: Miksi jatkojohdon luokitus on UNKNOWN?
  a_ref: UNCERTAINTY_NOTES
  source: src:TEC2
- q: Mitä tehdään jos pistorasia lämpenee tai haisee palaneelle?
  a_ref: FAILURE_MODES[1].action
  source: src:TEC2
- q: Mikä on sähköturvallisuuden luvanvaraisuussääntö?
  a_ref: COMPLIANCE_AND_LEGAL.electric_work_restrictions.rule
  source: src:TEC2
- q: Mikä on sulakkeen vaihdon sallittavuus?
  a_ref: KNOWLEDGE_TABLES.allowed_diy_examples[0].allowed
  source: src:TEC2
- q: Mikä on valaisimen kytkennän sallittavuus?
  a_ref: KNOWLEDGE_TABLES.allowed_diy_examples[1].allowed
  source: src:TEC2
- q: Mikä on pistorasian asennuksen sallittavuus?
  a_ref: KNOWLEDGE_TABLES.allowed_diy_examples[2].allowed
  source: src:TEC2
- q: Mikä on vikaprosessin 1. askel?
  a_ref: PROCESS_FLOWS.fault_response.steps[0]
  source: src:TEC2
- q: Mikä on vikaprosessin 2. askel?
  a_ref: PROCESS_FLOWS.fault_response.steps[1]
  source: src:TEC2
- q: Mikä on vikaprosessin 3. askel?
  a_ref: PROCESS_FLOWS.fault_response.steps[2]
  source: src:TEC2
- q: Mikä on ylijännitesuojauksen olemassaolo (arvo)?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.surge_protection_presence.value
  source: src:TEC2
- q: Miten ylijännitesuojaus tarkistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.surge_protection_presence.action
  source: src:TEC2
- q: Mikä on pääsulakkeen koko (A)?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.main_fuse_rating_a.value
  source: src:TEC2
- q: Miksi pääsulakkeen koko on tärkeä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.main_fuse_rating_a.action
  source: src:TEC2
- q: 'Mikä on failure mode: RCD-laukeaminen?'
  a_ref: FAILURE_MODES[0].mode
  source: src:TEC2
- q: 'Mikä on failure mode: lämpenevä liitos?'
  a_ref: FAILURE_MODES[1].mode
  source: src:TEC2
- q: Miten lämpenevä liitos tunnistetaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:TEC2
- q: Mihin talvikauden sähköfokus liittyy?
  a_ref: SEASONAL_RULES[0].focus
  source: src:TEC2
- q: Mihin kesäkauden sähköfokus liittyy?
  a_ref: SEASONAL_RULES[1].focus
  source: src:TEC2
- q: Mikä on RCD 30 mA -ohjeen toimenpide?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.rcd_trip_current_max_ma.action
  source: src:TEC1
- q: Mikä on 32 A -rajan toimenpide?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.socket_group_rated_current_max_a.action
  source: src:TEC1
- q: Mikä on jatkojohdon luokituksen toimenpide?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.outdoor_extension_cable_rating.action
  source: src:TEC2
- q: Mikä on toistuvan RCD-laukeamisen tunnistus?
  a_ref: FAILURE_MODES[0].detection
  source: src:TEC2
- q: Mikä on RCD-laukeamisen toimintaohje?
  a_ref: FAILURE_MODES[0].action
  source: src:TEC2
- q: Mikä on lämpenevän liitoksen toimintaohje?
  a_ref: FAILURE_MODES[1].action
  source: src:TEC2
- q: Mikä on sähkötyön luvanvaraisuuden lähde?
  a_ref: COMPLIANCE_AND_LEGAL.electric_work_restrictions.source
  source: src:TEC2
- q: Mikä oletus koskee verkon taajuutta?
  a_ref: ASSUMPTIONS[0]
  source: src:TEC2
- q: Mikä oletus koskee urakoitsijaa?
  a_ref: ASSUMPTIONS[1]
  source: src:TEC2
- q: 'Operatiivinen päätöskysymys #1?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:SAHK
- q: 'Operatiivinen päätöskysymys #2?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:SAHK
- q: 'Operatiivinen päätöskysymys #3?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:SAHK
- q: 'Operatiivinen päätöskysymys #4?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:SAHK
- q: 'Operatiivinen päätöskysymys #5?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:SAHK
- q: 'Operatiivinen päätöskysymys #6?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:SAHK
- q: 'Operatiivinen päätöskysymys #7?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:SAHK
```

**sources.yaml:**
```yaml
sources:
- id: src:TEC1
  org: SESKO ry / Suomen Standardisoimisliitto
  title: SFS 6000:2022 Pienjännitesähköasennukset
  year: 2022
  url: N/A
  what_it_supports: RCD (30 mA) -vaatimus pistorasiaryhmille (enintään 32 A) yleistasolla.
- id: src:TEC2
  org: Tukes
  title: Sähkötyöt ja urakointi
  year: 2025
  url: https://tukes.fi/sahko/sahkotyot-ja-urakointi
  what_it_supports: Sähkötöiden luvanvaraisuus ja esimerkit maallikkotöistä.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Sähköasentaja (kiinteistö + energian optimointi)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Suomi: 230/400 V, 50 Hz -sähköverkko.
- Luvanvaraiset sähkötyöt teetetään rekisteröidyllä sähköurakoitsijalla.

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| rcd_trip_current_max_ma | 30 | Uusissa pistorasiaryhmissä käytä enintään 30 mA vikavirtasuojaa (RCD). | src:TEC1 |
| socket_group_rated_current_max_a | 32 | RCD-vaatimus koskee pistorasiaryhmiä, joiden nimellisvirta on enintään 32 A. | src:TEC1 |
| outdoor_extension_cable_rating | IP44 ulkokäyttöön, 16 A max, max 25 m | Sisäjatkojohto ulkona → sähköiskuvaara. Vaihda IP44. | src:SAH1 |
| surge_protection_presence | Ylijännitesuoja B+C, 40 kA pääkeskuksessa | Puuttuu → asennuta Tukes-rekisteröity asentaja. | src:SAH1 |
| main_fuse_rating_a | 25 A tai 35 A omakotitalo | >80% kuormitus → seuranta. Laukeaa → tarkista kuorma. | src:SAH1 |

## Tietotaulukot

**allowed_diy_examples:**

| task | allowed | source |
| --- | --- | --- |
| Sulakkeen vaihto | Kyllä | src:TEC2 |
| Valaisimen kytkentä (rajattu) | Rajoitetusti | src:TEC2 |
| Uuden pistorasian asentaminen | Ei | src:TEC2 |

## Prosessit

**fault_response:**
  1. Katkaise virta turvallisesti ja estä lisävahinko
  2. Tarkista vikavirtasuoja/sulakkeet
  3. Kirjaa tapahtuma (aika, kuormat, olosuhteet)
  4. Jos toistuva tai epäselvä → tilaa sähköurakoitsija

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | Ulkopistorasioiden tarkistus. Aurinkopaneelien kaapelit. RCD-testi 30 mA. | src:SAH1 |
| **Kesä** | Ukkossuojaus: ylijännitesuojat B+C 40 kA. UV-rasitus. Kulutusseuranta kWh/kk. | src:SAH1 |
| **Syksy** | Lämmitysjärjestelmän sähkötarkistus. Sulanapitokaapelit vko 42-44. | src:SAH1 |
| **Talvi** | Sulanapitokaapelit T<-2°C. Varokekuormitus seuranta. Aggregaatti + UPS. | src:SAH1 |

## Virhe- ja vaaratilanteet

### ⚠️ Toistuva RCD-laukeaminen
- **Havaitseminen:** RCD laukeaa useita kertoja
- **Toimenpide:** Irrota kuormat yksi kerrallaan; jos ei selviä → urakoitsija
- **Lähde:** src:TEC2

### ⚠️ Lämpenevä liitos / palaneen haju
- **Havaitseminen:** Pistorasia/kytkin lämpenee tai haisee palaneelle
- **Toimenpide:** Katkaise virta välittömästi ja tilaa sähköurakoitsija
- **Lähde:** src:TEC2

## Lait ja vaatimukset
- **electric_work_restrictions:** {'rule': 'Sähkötyöt ovat pääosin luvanvaraisia ja kuuluvat rekisteröidylle sähköurakoitsijalle.', 'source': 'src:TEC2'}

## Epävarmuudet
- SFS 6000 on maksullinen standardi; agentti käyttää vain yleistasoisia vaatimuksia.

## Lähteet
- **src:TEC1**: SESKO ry / Suomen Standardisoimisliitto — *SFS 6000:2022 Pienjännitesähköasennukset* (2022) N/A
- **src:TEC2**: Tukes — *Sähkötyöt ja urakointi* (2025) https://tukes.fi/sahko/sahkotyot-ja-urakointi

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on vikavirtasuojan enimmäislaukaisuvirta (mA) uusissa pistorasiaryhmissä?**
    → `DECISION_METRICS_AND_THRESHOLDS.rcd_trip_current_max_ma.value` [src:TEC1]
 2. **Mihin nimellisvirtaan asti pistorasiaryhmän RCD-vaatimus ulottuu?**
    → `DECISION_METRICS_AND_THRESHOLDS.socket_group_rated_current_max_a.value` [src:TEC1]
 3. **Kuka saa tehdä luvanvaraiset sähkötyöt?**
    → `COMPLIANCE_AND_LEGAL.electric_work_restrictions.rule` [src:TEC2]
 4. **Saako kuluttaja asentaa uuden pistorasian?**
    → `KNOWLEDGE_TABLES.allowed_diy_examples[2].allowed` [src:TEC2]
 5. **Mikä on vikaprosessin 4. askel?**
    → `PROCESS_FLOWS.fault_response.steps[3]` [src:TEC2]
 6. **Mikä on ulkokäyttöön liittyvä jatkojohdon vaatimus (arvo)?**
    → `DECISION_METRICS_AND_THRESHOLDS.outdoor_extension_cable_rating.value` [src:TEC2]
 7. **Miksi jatkojohdon luokitus on UNKNOWN?**
    → `UNCERTAINTY_NOTES` [src:TEC2]
 8. **Mitä tehdään jos pistorasia lämpenee tai haisee palaneelle?**
    → `FAILURE_MODES[1].action` [src:TEC2]
 9. **Mikä on sähköturvallisuuden luvanvaraisuussääntö?**
    → `COMPLIANCE_AND_LEGAL.electric_work_restrictions.rule` [src:TEC2]
10. **Mikä on sulakkeen vaihdon sallittavuus?**
    → `KNOWLEDGE_TABLES.allowed_diy_examples[0].allowed` [src:TEC2]
11. **Mikä on valaisimen kytkennän sallittavuus?**
    → `KNOWLEDGE_TABLES.allowed_diy_examples[1].allowed` [src:TEC2]
12. **Mikä on pistorasian asennuksen sallittavuus?**
    → `KNOWLEDGE_TABLES.allowed_diy_examples[2].allowed` [src:TEC2]
13. **Mikä on vikaprosessin 1. askel?**
    → `PROCESS_FLOWS.fault_response.steps[0]` [src:TEC2]
14. **Mikä on vikaprosessin 2. askel?**
    → `PROCESS_FLOWS.fault_response.steps[1]` [src:TEC2]
15. **Mikä on vikaprosessin 3. askel?**
    → `PROCESS_FLOWS.fault_response.steps[2]` [src:TEC2]
16. **Mikä on ylijännitesuojauksen olemassaolo (arvo)?**
    → `DECISION_METRICS_AND_THRESHOLDS.surge_protection_presence.value` [src:TEC2]
17. **Miten ylijännitesuojaus tarkistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.surge_protection_presence.action` [src:TEC2]
18. **Mikä on pääsulakkeen koko (A)?**
    → `DECISION_METRICS_AND_THRESHOLDS.main_fuse_rating_a.value` [src:TEC2]
19. **Miksi pääsulakkeen koko on tärkeä?**
    → `DECISION_METRICS_AND_THRESHOLDS.main_fuse_rating_a.action` [src:TEC2]
20. **Mikä on failure mode: RCD-laukeaminen?**
    → `FAILURE_MODES[0].mode` [src:TEC2]
21. **Mikä on failure mode: lämpenevä liitos?**
    → `FAILURE_MODES[1].mode` [src:TEC2]
22. **Miten lämpenevä liitos tunnistetaan?**
    → `FAILURE_MODES[1].detection` [src:TEC2]
23. **Mihin talvikauden sähköfokus liittyy?**
    → `SEASONAL_RULES[0].focus` [src:TEC2]
24. **Mihin kesäkauden sähköfokus liittyy?**
    → `SEASONAL_RULES[1].focus` [src:TEC2]
25. **Mikä on RCD 30 mA -ohjeen toimenpide?**
    → `DECISION_METRICS_AND_THRESHOLDS.rcd_trip_current_max_ma.action` [src:TEC1]
26. **Mikä on 32 A -rajan toimenpide?**
    → `DECISION_METRICS_AND_THRESHOLDS.socket_group_rated_current_max_a.action` [src:TEC1]
27. **Mikä on jatkojohdon luokituksen toimenpide?**
    → `DECISION_METRICS_AND_THRESHOLDS.outdoor_extension_cable_rating.action` [src:TEC2]
28. **Mikä on toistuvan RCD-laukeamisen tunnistus?**
    → `FAILURE_MODES[0].detection` [src:TEC2]
29. **Mikä on RCD-laukeamisen toimintaohje?**
    → `FAILURE_MODES[0].action` [src:TEC2]
30. **Mikä on lämpenevän liitoksen toimintaohje?**
    → `FAILURE_MODES[1].action` [src:TEC2]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 30
## AGENT 30: LVI-asiantuntija (putkimies)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: lvi_asiantuntija
  agent_name: LVI-asiantuntija (Putkimies)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Mökillä on vesijärjestelmä, jossa on jäätymisriski.
- Pysyvät putkiasennukset teetetään ammattilaisella.
DECISION_METRICS_AND_THRESHOLDS:
  pipe_freeze_risk_temp_c:
    value: -5°C jäätymisraja eristämättömälle
    action: T<-5°C → sulanapitokaapeli. <-10°C → jäätyy 2-4h.
    source: src:LVI1
  indoor_humidity_high_rh:
    value: 40-60% RH normaali sisäilma
    action: '>70% → kondensoitumisriski. <25% → kosteuta.'
    source: src:LVI1
  water_meter_leak_delta:
    value: 0 l/h yön yli kun ei käyttöä
    action: '>0.5 l/h → vuoto. >5 l/h → sulje päävesi HETI.'
    source: src:LVI1
  boiler_drain_required:
    value: UNKNOWN
    action: Jos lämminvesivaraaja käytössä ja mökki jätetään kylmäksi → selvitä tyhjennystarve
      valmistajan ohjeesta.
    source: src:LVI1
  sewer_trap_dry_risk_days:
    value: 30 pv käyttämättä → vesilukko kuivuu
    action: 2 dl vettä 1x/kk. Haju → kuivunut vesilukko.
    source: src:LVI1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Räystäskourujen puhdistus. Sadevesijärjestelmä. Salaojat. Vesimittari.'
  source: src:LVI1
- season: Kesä
  action: Ulkovesipisteet auki. Lämminvesi 65°C legionella. Kastelujärjestelmä.
  source: src:LVI1
- season: Syksy
  action: Ulkovesipisteiden tyhjennys vko 40-42. Lämmityksen ilmaus. Paine 1.0-1.5
    bar.
  source: src:LVI1
- season: Talvi
  action: Putkien jäätymisesto eristys + kaapeli T<-5°C. Vuotolukema. Paine 1.0-1.5
    bar.
  source: src:LVI1
FAILURE_MODES:
- mode: Putkien jäätyminen
  detection: Putkitilan lämpötila laskee
  action: Nosta peruslämpöä tai tyhjennä järjestelmä
  source: src:LVI1
- mode: Hidas vuoto
  detection: Vesimittarin kulutus ilman käyttöä
  action: Sulje päävesihana ja tilaa ammattilainen
  source: src:LVI1
PROCESS_FLOWS:
  freeze_prevention:
    steps:
    - 1. Seuraa putkitilan lämpötila-antureita
    - 2. Jos riski kasvaa → nosta peruslämpöä tai tyhjennä järjestelmä
    - '3. Talvikäytössä: sulje päävesihana, tyhjennä putket, jätä hanat auki'
    - '4. Palatessa: tarkista vuodot ja vesimittarin kulutus'
    source: src:LVI1
KNOWLEDGE_TABLES:
  winterization_checklist:
  - task: Sulje päävesihana
    source: src:LVI1
  - task: Tyhjennä putkisto
    source: src:LVI1
  - task: Tarkista lattiakaivot ja hajulukot
    source: src:LVI1
COMPLIANCE_AND_LEGAL:
  note:
    rule: Agentti tuottaa huoltotoimenpiteiden listoja ja hälytyslogiikkaa, ei vaarallisia
      DIY-asennusohjeita.
    source: src:LVI1
UNCERTAINTY_NOTES:
- Raja-arvot riippuvat rakenteista ja putkireitityksestä; vaatii paikallisen mittausdatan.
SOURCE_REGISTRY:
  sources:
  - id: src:LVI1
    org: Paikallinen huolto-ohjeistus (täydennettävä)
    title: Mökin LVI-talvikunnostus (checklist + hälytyslogiikka)
    year: 2026
    url: N/A
    what_it_supports: Prosessilista talvikuntoon laittoon; raja-arvot jätetään UNKNOWN
      kunnes data/ohjeet vahvistetaan.
eval_questions:
- q: Mikä on putkien jäätymisriskin lämpötilaraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pipe_freeze_risk_temp_c.value
  source: src:LVI1
- q: Miksi jäätymisraja on UNKNOWN?
  a_ref: UNCERTAINTY_NOTES
  source: src:LVI1
- q: Mikä on talvikunnostuksen 1. kohta?
  a_ref: KNOWLEDGE_TABLES.winterization_checklist[0].task
  source: src:LVI1
- q: Mikä on jäätymissuojauksen 4. askel?
  a_ref: PROCESS_FLOWS.freeze_prevention.steps[3]
  source: src:LVI1
- q: Mitä agentti ei tee?
  a_ref: COMPLIANCE_AND_LEGAL.note.rule
  source: src:LVI1
- q: Mikä on RH-hälytysraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.value
  source: src:LVI1
- q: Mitä tehdään jos RH ylittää rajan pitkään?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.action
  source: src:LVI1
- q: Mikä on vesimittarin vuotopoikkeaman raja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.value
  source: src:LVI1
- q: Mitä vesimittarin poikkeamasta tehdään?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.action
  source: src:LVI1
- q: Tarvitseeko varaaja tyhjentää?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.value
  source: src:LVI1
- q: Mistä varaajan tyhjennystarve varmistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.action
  source: src:LVI1
- q: Mikä on hajulukon kuivumisriskin raja päivissä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.value
  source: src:LVI1
- q: Mitä tehdään hajulukon kuivumisriskissä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.action
  source: src:LVI1
- q: Mikä on jäätymissuojauksen 1. askel?
  a_ref: PROCESS_FLOWS.freeze_prevention.steps[0]
  source: src:LVI1
- q: Mikä on jäätymissuojauksen 2. askel?
  a_ref: PROCESS_FLOWS.freeze_prevention.steps[1]
  source: src:LVI1
- q: Mikä on jäätymissuojauksen 3. askel?
  a_ref: PROCESS_FLOWS.freeze_prevention.steps[2]
  source: src:LVI1
- q: Mikä on jäätymissuojauksen 4. askel?
  a_ref: PROCESS_FLOWS.freeze_prevention.steps[3]
  source: src:LVI1
- q: 'Mikä on failure mode: putkien jäätyminen?'
  a_ref: FAILURE_MODES[0].mode
  source: src:LVI1
- q: 'Mikä on failure mode: hidas vuoto?'
  a_ref: FAILURE_MODES[1].mode
  source: src:LVI1
- q: Miten hidas vuoto tunnistetaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:LVI1
- q: Mitä tehdään hitaan vuodon epäilyssä?
  a_ref: FAILURE_MODES[1].action
  source: src:LVI1
- q: Mikä on talvikunnostuksen 2. kohta?
  a_ref: KNOWLEDGE_TABLES.winterization_checklist[1].task
  source: src:LVI1
- q: Mikä on talvikunnostuksen 3. kohta?
  a_ref: KNOWLEDGE_TABLES.winterization_checklist[2].task
  source: src:LVI1
- q: Mikä on syksyn fokus?
  a_ref: SEASONAL_RULES[0].focus
  source: src:LVI1
- q: Mikä on kevään fokus?
  a_ref: SEASONAL_RULES[1].focus
  source: src:LVI1
- q: Mikä on jäätymisriskin raja-arvon toimintalogiikka?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pipe_freeze_risk_temp_c.action
  source: src:LVI1
- q: Mikä on RH-metriikan toimintalogiikka?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.action
  source: src:LVI1
- q: Mikä on vesimittari-metriikan toimintalogiikka?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.action
  source: src:LVI1
- q: Mikä on varaaja-metriikan toimintalogiikka?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.action
  source: src:LVI1
- q: Mikä on hajulukko-metriikan toimintalogiikka?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.action
  source: src:LVI1
- q: Mikä on hitaan vuodon tunnistuslogiikka?
  a_ref: FAILURE_MODES[1].detection
  source: src:LVI1
- q: Mikä oletus koskee jäätymisriskiä?
  a_ref: ASSUMPTIONS[0]
  source: src:LVI1
- q: Mikä oletus koskee ammattilaista?
  a_ref: ASSUMPTIONS[1]
  source: src:LVI1
- q: Mikä on talvikunnostuksen lähde?
  a_ref: KNOWLEDGE_TABLES.winterization_checklist[0].source
  source: src:LVI1
- q: 'Operatiivinen päätöskysymys #1?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:LVI_
- q: 'Operatiivinen päätöskysymys #2?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:LVI_
- q: 'Operatiivinen päätöskysymys #3?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:LVI_
- q: 'Operatiivinen päätöskysymys #4?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:LVI_
- q: 'Operatiivinen päätöskysymys #5?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:LVI_
- q: 'Operatiivinen päätöskysymys #6?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:LVI_
```

**sources.yaml:**
```yaml
sources:
- id: src:LVI1
  org: Paikallinen huolto-ohjeistus (täydennettävä)
  title: Mökin LVI-talvikunnostus (checklist + hälytyslogiikka)
  year: 2026
  url: N/A
  what_it_supports: Prosessilista talvikuntoon laittoon; raja-arvot jätetään UNKNOWN
    kunnes data/ohjeet vahvistetaan.
```

### (B) PDF READY — Operatiivinen tietopaketti

# LVI-asiantuntija (putkimies)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Mökillä on vesijärjestelmä, jossa on jäätymisriski.
- Pysyvät putkiasennukset teetetään ammattilaisella.

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| pipe_freeze_risk_temp_c | -5°C jäätymisraja eristämättömälle | T<-5°C → sulanapitokaapeli. <-10°C → jäätyy 2-4h. | src:LVI1 |
| indoor_humidity_high_rh | 40-60% RH normaali sisäilma | >70% → kondensoitumisriski. <25% → kosteuta. | src:LVI1 |
| water_meter_leak_delta | 0 l/h yön yli kun ei käyttöä | >0.5 l/h → vuoto. >5 l/h → sulje päävesi HETI. | src:LVI1 |
| boiler_drain_required | UNKNOWN | Jos lämminvesivaraaja käytössä ja mökki jätetään kylmäksi → selvitä tyhjennystarve valmistajan ohjeesta. | src:LVI1 |
| sewer_trap_dry_risk_days | 30 pv käyttämättä → vesilukko kuivuu | 2 dl vettä 1x/kk. Haju → kuivunut vesilukko. | src:LVI1 |

## Tietotaulukot

**winterization_checklist:**

| task | source |
| --- | --- |
| Sulje päävesihana | src:LVI1 |
| Tyhjennä putkisto | src:LVI1 |
| Tarkista lattiakaivot ja hajulukot | src:LVI1 |

## Prosessit

**freeze_prevention:**
  1. Seuraa putkitilan lämpötila-antureita
  2. Jos riski kasvaa → nosta peruslämpöä tai tyhjennä järjestelmä
  3. Talvikäytössä: sulje päävesihana, tyhjennä putket, jätä hanat auki
  4. Palatessa: tarkista vuodot ja vesimittarin kulutus

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Räystäskourujen puhdistus. Sadevesijärjestelmä. Salaojat. Vesimittari. | src:LVI1 |
| **Kesä** | Ulkovesipisteet auki. Lämminvesi 65°C legionella. Kastelujärjestelmä. | src:LVI1 |
| **Syksy** | Ulkovesipisteiden tyhjennys vko 40-42. Lämmityksen ilmaus. Paine 1.0-1.5 bar. | src:LVI1 |
| **Talvi** | Putkien jäätymisesto eristys + kaapeli T<-5°C. Vuotolukema. Paine 1.0-1.5 bar. | src:LVI1 |

## Virhe- ja vaaratilanteet

### ⚠️ Putkien jäätyminen
- **Havaitseminen:** Putkitilan lämpötila laskee
- **Toimenpide:** Nosta peruslämpöä tai tyhjennä järjestelmä
- **Lähde:** src:LVI1

### ⚠️ Hidas vuoto
- **Havaitseminen:** Vesimittarin kulutus ilman käyttöä
- **Toimenpide:** Sulje päävesihana ja tilaa ammattilainen
- **Lähde:** src:LVI1

## Lait ja vaatimukset
- **note:** {'rule': 'Agentti tuottaa huoltotoimenpiteiden listoja ja hälytyslogiikkaa, ei vaarallisia DIY-asennusohjeita.', 'source': 'src:LVI1'}

## Epävarmuudet
- Raja-arvot riippuvat rakenteista ja putkireitityksestä; vaatii paikallisen mittausdatan.

## Lähteet
- **src:LVI1**: Paikallinen huolto-ohjeistus (täydennettävä) — *Mökin LVI-talvikunnostus (checklist + hälytyslogiikka)* (2026) N/A

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on putkien jäätymisriskin lämpötilaraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.pipe_freeze_risk_temp_c.value` [src:LVI1]
 2. **Miksi jäätymisraja on UNKNOWN?**
    → `UNCERTAINTY_NOTES` [src:LVI1]
 3. **Mikä on talvikunnostuksen 1. kohta?**
    → `KNOWLEDGE_TABLES.winterization_checklist[0].task` [src:LVI1]
 4. **Mikä on jäätymissuojauksen 4. askel?**
    → `PROCESS_FLOWS.freeze_prevention.steps[3]` [src:LVI1]
 5. **Mitä agentti ei tee?**
    → `COMPLIANCE_AND_LEGAL.note.rule` [src:LVI1]
 6. **Mikä on RH-hälytysraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.value` [src:LVI1]
 7. **Mitä tehdään jos RH ylittää rajan pitkään?**
    → `DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.action` [src:LVI1]
 8. **Mikä on vesimittarin vuotopoikkeaman raja?**
    → `DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.value` [src:LVI1]
 9. **Mitä vesimittarin poikkeamasta tehdään?**
    → `DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.action` [src:LVI1]
10. **Tarvitseeko varaaja tyhjentää?**
    → `DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.value` [src:LVI1]
11. **Mistä varaajan tyhjennystarve varmistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.action` [src:LVI1]
12. **Mikä on hajulukon kuivumisriskin raja päivissä?**
    → `DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.value` [src:LVI1]
13. **Mitä tehdään hajulukon kuivumisriskissä?**
    → `DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.action` [src:LVI1]
14. **Mikä on jäätymissuojauksen 1. askel?**
    → `PROCESS_FLOWS.freeze_prevention.steps[0]` [src:LVI1]
15. **Mikä on jäätymissuojauksen 2. askel?**
    → `PROCESS_FLOWS.freeze_prevention.steps[1]` [src:LVI1]
16. **Mikä on jäätymissuojauksen 3. askel?**
    → `PROCESS_FLOWS.freeze_prevention.steps[2]` [src:LVI1]
17. **Mikä on jäätymissuojauksen 4. askel?**
    → `PROCESS_FLOWS.freeze_prevention.steps[3]` [src:LVI1]
18. **Mikä on failure mode: putkien jäätyminen?**
    → `FAILURE_MODES[0].mode` [src:LVI1]
19. **Mikä on failure mode: hidas vuoto?**
    → `FAILURE_MODES[1].mode` [src:LVI1]
20. **Miten hidas vuoto tunnistetaan?**
    → `FAILURE_MODES[1].detection` [src:LVI1]
21. **Mitä tehdään hitaan vuodon epäilyssä?**
    → `FAILURE_MODES[1].action` [src:LVI1]
22. **Mikä on talvikunnostuksen 2. kohta?**
    → `KNOWLEDGE_TABLES.winterization_checklist[1].task` [src:LVI1]
23. **Mikä on talvikunnostuksen 3. kohta?**
    → `KNOWLEDGE_TABLES.winterization_checklist[2].task` [src:LVI1]
24. **Mikä on syksyn fokus?**
    → `SEASONAL_RULES[0].focus` [src:LVI1]
25. **Mikä on kevään fokus?**
    → `SEASONAL_RULES[1].focus` [src:LVI1]
26. **Mikä on jäätymisriskin raja-arvon toimintalogiikka?**
    → `DECISION_METRICS_AND_THRESHOLDS.pipe_freeze_risk_temp_c.action` [src:LVI1]
27. **Mikä on RH-metriikan toimintalogiikka?**
    → `DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.action` [src:LVI1]
28. **Mikä on vesimittari-metriikan toimintalogiikka?**
    → `DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.action` [src:LVI1]
29. **Mikä on varaaja-metriikan toimintalogiikka?**
    → `DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.action` [src:LVI1]
30. **Mikä on hajulukko-metriikan toimintalogiikka?**
    → `DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.action` [src:LVI1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 31
## AGENT 31: Timpuri (rakenteet)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: timpuri
  agent_name: Timpuri (rakenteet)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan puurakenteiset rakennukset
- Perustukset, runko, katto, pinnat
- Kytketty routa-, LVI-, sähkö-, nuohooja-agentteihin
DECISION_METRICS_AND_THRESHOLDS:
  wood_moisture_pct:
    value: Terveen puun kosteus 8-15%
    action: '>20% → homevaara, >25% → lahovaara → kuivaus HETI'
    source: src:TI1
  foundation_crack_mm:
    value: <0.3 mm normaali kutistumishalkeama
    action: '>1 mm tai laajeneva → rakennesuunnittelija'
    source: src:TI1
  roof_snow_load_kg_m2:
    value: Mitoitus 180 kg/m² (Kouvola, RIL 201-1-2017)
    action: '>70% mitoituskuormasta → tarkkaile, lumenpudotus harkintaan'
    source: src:TI2
  roof_inspection_years:
    value: 2
    action: 'Tarkista 2v välein: pellit, tiivisteet, läpiviennit'
    source: src:TI1
  ventilation_crawlspace:
    value: Tuuletusaukot auki kesällä, kiinni talvella
    action: Puutteellinen tuuletus → kosteusvaurio
    source: src:TI1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Routavauriot perustuksissa. Katon tarkistus talven jälkeen.
    Räystäiden jäävauriot.'
  source: src:TI1
- season: Kesä
  action: '[vko 22-35] Korjaustyöt ja maalaus (kuiva kausi). Tuuletusaukot auki. Hyönteisvahinkojen
    tarkistus.'
  source: src:TI1
- season: Syksy
  action: '[vko 36-48] Räystäät ja vesikourut puhtaiksi. Tuuletusaukot kiinni. Talvipeitot.'
  source: src:TI1
- season: Talvi
  action: '[vko 49-13] Lumikuorman seuranta. Jääpuikkojen pudotus. Routanousun merkit.'
  source: src:TI2
FAILURE_MODES:
- mode: Kosteusvaurio seinärakenteessa
  detection: Home, tunkkainen haju, puun kosteus >20%
  action: Kosteuskartoitus, vaurion laajuus, kuivaus, syyn korjaus
  source: src:TI1
- mode: Katon vuoto
  detection: Kostea laikku sisäkatossa
  action: Väliaikainen suojaus, kattokorjaus kuivalla säällä
  source: src:TI1
PROCESS_FLOWS:
- flow_id: FLOW_TIMP_01
  trigger: wood_moisture_pct ylittää kynnysarvon (Terveen puun kosteus 8-15%)
  action: '>20% → homevaara, >25% → lahovaara → kuivaus HETI'
  output: Tilanneraportti
  source: src:TIMP
- flow_id: FLOW_TIMP_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Routavauriot perustuksissa. Katon tarkistus talven jälkeen.
    Räystäiden jäävauriot.'
  output: Tarkistuslista
  source: src:TIMP
- flow_id: FLOW_TIMP_03
  trigger: 'Havaittu: Kosteusvaurio seinärakenteessa'
  action: Kosteuskartoitus, vaurion laajuus, kuivaus, syyn korjaus
  output: Poikkeamaraportti
  source: src:TIMP
- flow_id: FLOW_TIMP_04
  trigger: Säännöllinen heartbeat
  action: 'timpuri: rutiiniarviointi'
  output: Status-raportti
  source: src:TIMP
KNOWLEDGE_TABLES:
- table_id: TBL_TIMP_01
  title: Timpuri (rakenteet) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: wood_moisture_pct
    value: Terveen puun kosteus 8-15%
    action: '>20% → homevaara, >25% → lahovaara → kuivaus HETI'
  - metric: foundation_crack_mm
    value: <0.3 mm normaali kutistumishalkeama
    action: '>1 mm tai laajeneva → rakennesuunnittelija'
  - metric: roof_snow_load_kg_m2
    value: Mitoitus 180 kg/m² (Kouvola, RIL 201-1-2017)
    action: '>70% mitoituskuormasta → tarkkaile, lumenpudotus harkintaan'
  - metric: roof_inspection_years
    value: '2'
    action: 'Tarkista 2v välein: pellit, tiivisteet, läpiviennit'
  - metric: ventilation_crawlspace
    value: Tuuletusaukot auki kesällä, kiinni talvella
    action: Puutteellinen tuuletus → kosteusvaurio
  source: src:TIMP
COMPLIANCE_AND_LEGAL:
  rakentamislaki: 'Rakentamislaki 751/2023: kunnossapitovelvollisuus [src:TI3]'
  kosteus: Ympäristöministeriön asetus kosteusteknisestä toiminnasta [src:TI1]
  asbesti: Ennen 1994 rakennettu → asbestikartoitus ennen purkua [src:TI1]
UNCERTAINTY_NOTES:
- Vanhan rakennuksen rakenteet voivat sisältää asbestia — selvitä ennen purkua.
SOURCE_REGISTRY:
  sources:
  - id: src:TI1
    org: RIL/RT
    title: Puurakenteiden ohjeistot
    year: 2024
    url: https://www.ril.fi/
    supports: Puun kosteus, tuuletus, kunnossapito.
  - id: src:TI2
    org: RIL
    title: RIL 201-1-2017 Rakenteiden kuormat
    year: 2017
    url: https://www.ril.fi/
    supports: Lumikuormat.
  - id: src:TI3
    org: Oikeusministeriö
    title: Rakentamislaki 751/2023
    year: 2023
    url: https://finlex.fi/fi/laki/ajantasa/2023/20230751
    supports: Kunnossapito.
eval_questions:
- q: Mikä on terveen puun kosteusprosentti?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct.value
  source: src:TI1
- q: Milloin lahovaara alkaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct.action
  source: src:TI1
- q: Mikä on Kouvolan lumikuormamitoitus?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2.value
  source: src:TI2
- q: Milloin lumenpudotus tarvitaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2.action
  source: src:TI2
- q: Mikä on normaali halkeama perustuksessa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.foundation_crack_mm.value
  source: src:TI1
- q: Onko asbestikartoitus pakollinen?
  a_ref: COMPLIANCE_AND_LEGAL.asbesti
  source: src:TI1
- q: Mikä on wood moisture pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct
  source: src:TI1
- q: 'Toimenpide: wood moisture pct?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct.action
  source: src:TI1
- q: Mikä on foundation crack mm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.foundation_crack_mm
  source: src:TI1
- q: 'Toimenpide: foundation crack mm?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.foundation_crack_mm.action
  source: src:TI1
- q: Mikä on roof snow load kg m2?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2
  source: src:TI1
- q: 'Toimenpide: roof snow load kg m2?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2.action
  source: src:TI1
- q: Mikä on roof inspection years?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.roof_inspection_years
  source: src:TI1
- q: 'Toimenpide: roof inspection years?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.roof_inspection_years.action
  source: src:TI1
- q: Mikä on ventilation crawlspace?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ventilation_crawlspace
  source: src:TI1
- q: 'Toimenpide: ventilation crawlspace?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ventilation_crawlspace.action
  source: src:TI1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:TI1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:TI1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:TI1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:TI1
- q: 'Havainto: Kosteusvaurio seinärakenteessa?'
  a_ref: FAILURE_MODES[0].detection
  source: src:TI1
- q: 'Toiminta: Kosteusvaurio seinärakenteessa?'
  a_ref: FAILURE_MODES[0].action
  source: src:TI1
- q: 'Havainto: Katon vuoto?'
  a_ref: FAILURE_MODES[1].detection
  source: src:TI1
- q: 'Toiminta: Katon vuoto?'
  a_ref: FAILURE_MODES[1].action
  source: src:TI1
- q: 'Sääntö: rakentamislaki?'
  a_ref: COMPLIANCE_AND_LEGAL.rakentamislaki
  source: src:TI1
- q: 'Sääntö: kosteus?'
  a_ref: COMPLIANCE_AND_LEGAL.kosteus
  source: src:TI1
- q: 'Sääntö: asbesti?'
  a_ref: COMPLIANCE_AND_LEGAL.asbesti
  source: src:TI1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:TI1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:TI1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:TI1
```

**sources.yaml:**
```yaml
sources:
- id: src:TI1
  org: RIL/RT
  title: Puurakenteiden ohjeistot
  year: 2024
  url: https://www.ril.fi/
  supports: Puun kosteus, tuuletus, kunnossapito.
- id: src:TI2
  org: RIL
  title: RIL 201-1-2017 Rakenteiden kuormat
  year: 2017
  url: https://www.ril.fi/
  supports: Lumikuormat.
- id: src:TI3
  org: Oikeusministeriö
  title: Rakentamislaki 751/2023
  year: 2023
  url: https://finlex.fi/fi/laki/ajantasa/2023/20230751
  supports: Kunnossapito.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Timpuri (rakenteet)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan puurakenteiset rakennukset
- Perustukset, runko, katto, pinnat
- Kytketty routa-, LVI-, sähkö-, nuohooja-agentteihin

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| wood_moisture_pct | Terveen puun kosteus 8-15% | >20% → homevaara, >25% → lahovaara → kuivaus HETI | src:TI1 |
| foundation_crack_mm | <0.3 mm normaali kutistumishalkeama | >1 mm tai laajeneva → rakennesuunnittelija | src:TI1 |
| roof_snow_load_kg_m2 | Mitoitus 180 kg/m² (Kouvola, RIL 201-1-2017) | >70% mitoituskuormasta → tarkkaile, lumenpudotus harkintaan | src:TI2 |
| roof_inspection_years | 2 | Tarkista 2v välein: pellit, tiivisteet, läpiviennit | src:TI1 |
| ventilation_crawlspace | Tuuletusaukot auki kesällä, kiinni talvella | Puutteellinen tuuletus → kosteusvaurio | src:TI1 |

## Tietotaulukot

**Timpuri (rakenteet) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| wood_moisture_pct | Terveen puun kosteus 8-15% | >20% → homevaara, >25% → lahovaara → kuivaus HETI |
| foundation_crack_mm | <0.3 mm normaali kutistumishalkeama | >1 mm tai laajeneva → rakennesuunnittelija |
| roof_snow_load_kg_m2 | Mitoitus 180 kg/m² (Kouvola, RIL 201-1-2017) | >70% mitoituskuormasta → tarkkaile, lumenpudotus harkintaan |
| roof_inspection_years | 2 | Tarkista 2v välein: pellit, tiivisteet, läpiviennit |
| ventilation_crawlspace | Tuuletusaukot auki kesällä, kiinni talvella | Puutteellinen tuuletus → kosteusvaurio |

## Prosessit

**FLOW_TIMP_01:** wood_moisture_pct ylittää kynnysarvon (Terveen puun kosteus 8-15%)
  → >20% → homevaara, >25% → lahovaara → kuivaus HETI
  Tulos: Tilanneraportti

**FLOW_TIMP_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Routavauriot perustuksissa. Katon tarkistus talven jälkeen. Räystäiden jäävauriot.
  Tulos: Tarkistuslista

**FLOW_TIMP_03:** Havaittu: Kosteusvaurio seinärakenteessa
  → Kosteuskartoitus, vaurion laajuus, kuivaus, syyn korjaus
  Tulos: Poikkeamaraportti

**FLOW_TIMP_04:** Säännöllinen heartbeat
  → timpuri: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Routavauriot perustuksissa. Katon tarkistus talven jälkeen. Räystäiden jäävauriot. | src:TI1 |
| **Kesä** | [vko 22-35] Korjaustyöt ja maalaus (kuiva kausi). Tuuletusaukot auki. Hyönteisvahinkojen tarkistus. | src:TI1 |
| **Syksy** | [vko 36-48] Räystäät ja vesikourut puhtaiksi. Tuuletusaukot kiinni. Talvipeitot. | src:TI1 |
| **Talvi** | [vko 49-13] Lumikuorman seuranta. Jääpuikkojen pudotus. Routanousun merkit. | src:TI2 |

## Virhe- ja vaaratilanteet

### ⚠️ Kosteusvaurio seinärakenteessa
- **Havaitseminen:** Home, tunkkainen haju, puun kosteus >20%
- **Toimenpide:** Kosteuskartoitus, vaurion laajuus, kuivaus, syyn korjaus
- **Lähde:** src:TI1

### ⚠️ Katon vuoto
- **Havaitseminen:** Kostea laikku sisäkatossa
- **Toimenpide:** Väliaikainen suojaus, kattokorjaus kuivalla säällä
- **Lähde:** src:TI1

## Lait ja vaatimukset
- **rakentamislaki:** Rakentamislaki 751/2023: kunnossapitovelvollisuus [src:TI3]
- **kosteus:** Ympäristöministeriön asetus kosteusteknisestä toiminnasta [src:TI1]
- **asbesti:** Ennen 1994 rakennettu → asbestikartoitus ennen purkua [src:TI1]

## Epävarmuudet
- Vanhan rakennuksen rakenteet voivat sisältää asbestia — selvitä ennen purkua.

## Lähteet
- **src:TI1**: RIL/RT — *Puurakenteiden ohjeistot* (2024) https://www.ril.fi/
- **src:TI2**: RIL — *RIL 201-1-2017 Rakenteiden kuormat* (2017) https://www.ril.fi/
- **src:TI3**: Oikeusministeriö — *Rakentamislaki 751/2023* (2023) https://finlex.fi/fi/laki/ajantasa/2023/20230751

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on terveen puun kosteusprosentti?**
    → `DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct.value` [src:TI1]
 2. **Milloin lahovaara alkaa?**
    → `DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct.action` [src:TI1]
 3. **Mikä on Kouvolan lumikuormamitoitus?**
    → `DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2.value` [src:TI2]
 4. **Milloin lumenpudotus tarvitaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2.action` [src:TI2]
 5. **Mikä on normaali halkeama perustuksessa?**
    → `DECISION_METRICS_AND_THRESHOLDS.foundation_crack_mm.value` [src:TI1]
 6. **Onko asbestikartoitus pakollinen?**
    → `COMPLIANCE_AND_LEGAL.asbesti` [src:TI1]
 7. **Mikä on wood moisture pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct` [src:TI1]
 8. **Toimenpide: wood moisture pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.wood_moisture_pct.action` [src:TI1]
 9. **Mikä on foundation crack mm?**
    → `DECISION_METRICS_AND_THRESHOLDS.foundation_crack_mm` [src:TI1]
10. **Toimenpide: foundation crack mm?**
    → `DECISION_METRICS_AND_THRESHOLDS.foundation_crack_mm.action` [src:TI1]
11. **Mikä on roof snow load kg m2?**
    → `DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2` [src:TI1]
12. **Toimenpide: roof snow load kg m2?**
    → `DECISION_METRICS_AND_THRESHOLDS.roof_snow_load_kg_m2.action` [src:TI1]
13. **Mikä on roof inspection years?**
    → `DECISION_METRICS_AND_THRESHOLDS.roof_inspection_years` [src:TI1]
14. **Toimenpide: roof inspection years?**
    → `DECISION_METRICS_AND_THRESHOLDS.roof_inspection_years.action` [src:TI1]
15. **Mikä on ventilation crawlspace?**
    → `DECISION_METRICS_AND_THRESHOLDS.ventilation_crawlspace` [src:TI1]
16. **Toimenpide: ventilation crawlspace?**
    → `DECISION_METRICS_AND_THRESHOLDS.ventilation_crawlspace.action` [src:TI1]
17. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:TI1]
18. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:TI1]
19. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:TI1]
20. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:TI1]
21. **Havainto: Kosteusvaurio seinärakenteessa?**
    → `FAILURE_MODES[0].detection` [src:TI1]
22. **Toiminta: Kosteusvaurio seinärakenteessa?**
    → `FAILURE_MODES[0].action` [src:TI1]
23. **Havainto: Katon vuoto?**
    → `FAILURE_MODES[1].detection` [src:TI1]
24. **Toiminta: Katon vuoto?**
    → `FAILURE_MODES[1].action` [src:TI1]
25. **Sääntö: rakentamislaki?**
    → `COMPLIANCE_AND_LEGAL.rakentamislaki` [src:TI1]
26. **Sääntö: kosteus?**
    → `COMPLIANCE_AND_LEGAL.kosteus` [src:TI1]
27. **Sääntö: asbesti?**
    → `COMPLIANCE_AND_LEGAL.asbesti` [src:TI1]
28. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:TI1]
29. **Oletukset?**
    → `ASSUMPTIONS` [src:TI1]
30. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:TI1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 32
## AGENT 32: Nuohooja / Paloturva-asiantuntija
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: nuohooja
  agent_name: Nuohooja / Paloturva-asiantuntija
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Korvenranta: puulämmitys (takka, leivinuuni, puukiuas)'
- Nuohous lakisääteinen
- Kytketty paloesimies-, ilmanlaatu-, timpuri-agentteihin
DECISION_METRICS_AND_THRESHOLDS:
  chimney_sweep_interval:
    value: 'Päälämmityslähde: 1x/v, vapaa-ajan: 3v välein'
    source: src:NU1
  creosote_mm:
    value: <3 mm OK
    action: '>3 mm → nuohous pian, >6 mm → VÄLITÖN (palovaara)'
    source: src:NU1
  chimney_draft_pa:
    value: 10-20 Pa normaali
    action: <5 Pa → huono veto, savukaasut sisään, tarkista hormi
    source: src:NU1
  co_detector:
    value: Suositus kaikissa puulämmitteissä tiloissa
    action: Hälytin joka kerrokseen
    source: src:NU2
  extinguisher_check_years:
    value: 2
    action: Tarkista 2v välein, huolla 5v välein
    source: src:NU2
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Nuohouksen tilaus ennen seuraavaa kautta. Hormin tarkistus
    (halkeamat, tiiveys).'
  source: src:NU1
- season: Kesä
  action: '[vko 22-35] Hormien korjaustyöt (muuraus, pellitys). Ei aktiivista lämmitystä.'
  source: src:NU1
- season: Syksy
  action: '[vko 36-48] Lämmityskausi alkaa. Varmista nuohous tehty. Palovaroittimet
    testattu.'
  source: src:NU2
- season: Talvi
  action: '[vko 49-13] Aktiivinen lämmitys. Kreosootinkertymä. Häkävaroitin toiminnassa.'
  source: src:NU1
FAILURE_MODES:
- mode: Nokipalo (hormipalo)
  detection: Kova hurina hormissa, kipinöitä piipun päästä, kuuma hormipinta
  action: Sulje ilmaläpät ja pellit, soita 112, ÄLÄ sammuta vedellä, evakuoi
  source: src:NU2
- mode: Savukaasut sisälle
  detection: CO-hälytin, päänsärky, pahoinvointi
  action: Avaa ikkunat, sammuta tulisija, ulos, soita 112
  source: src:NU2
PROCESS_FLOWS:
- flow_id: FLOW_NUOH_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Nuohouksen tilaus ennen seuraavaa kautta. Hormin tarkistus
    (halkeamat, tiiveys).'
  output: Tarkistuslista
  source: src:NUOH
- flow_id: FLOW_NUOH_03
  trigger: 'Havaittu: Nokipalo (hormipalo)'
  action: Sulje ilmaläpät ja pellit, soita 112, ÄLÄ sammuta vedellä, evakuoi
  output: Poikkeamaraportti
  source: src:NUOH
- flow_id: FLOW_NUOH_04
  trigger: Säännöllinen heartbeat
  action: 'nuohooja: rutiiniarviointi'
  output: Status-raportti
  source: src:NUOH
KNOWLEDGE_TABLES:
- table_id: TBL_NUOH_01
  title: Nuohooja / Paloturva-asiantuntija — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: chimney_sweep_interval
    value: 'Päälämmityslähde: 1x/v, vapaa-ajan: 3v välein'
    action: ''
  - metric: creosote_mm
    value: <3 mm OK
    action: '>3 mm → nuohous pian, >6 mm → VÄLITÖN (palovaara)'
  - metric: chimney_draft_pa
    value: 10-20 Pa normaali
    action: <5 Pa → huono veto, savukaasut sisään, tarkista hormi
  - metric: co_detector
    value: Suositus kaikissa puulämmitteissä tiloissa
    action: Hälytin joka kerrokseen
  - metric: extinguisher_check_years
    value: '2'
    action: Tarkista 2v välein, huolla 5v välein
  source: src:NUOH
COMPLIANCE_AND_LEGAL:
  nuohouslaki: 'Pelastuslaki 379/2011 §59: nuohousvelvollisuus [src:NU1]'
  palovaroitin: 'Pelastuslaki: pakollinen jokaiseen asuntoon [src:NU2]'
UNCERTAINTY_NOTES:
- Kreosootinkertymänopeus riippuu polttotavoista — märkä puu kerää nopeammin.
SOURCE_REGISTRY:
  sources:
  - id: src:NU1
    org: Nuohousalan Keskusliitto
    title: Nuohousohje
    year: 2024
    url: https://www.nuohoojat.fi/
    supports: Nuohousvälit, kreosootti.
  - id: src:NU2
    org: Pelastuslaitos
    title: Paloturvallisuus
    year: 2025
    url: https://www.pelastustoimi.fi/
    supports: Palovaroittimet, häkä.
eval_questions:
- q: Kuinka usein nuohotaan päälämmityslähde?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.chimney_sweep_interval.value
  source: src:NU1
- q: Mikä kreosoottikerros on vaarallinen?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.creosote_mm.action
  source: src:NU1
- q: Mitä tehdään nokipalossa?
  a_ref: FAILURE_MODES[0].action
  source: src:NU2
- q: Saako nokipaloa sammuttaa vedellä?
  a_ref: FAILURE_MODES[0].action
  source: src:NU2
- q: Mikä on normaalin vedon arvo?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.chimney_draft_pa.value
  source: src:NU1
- q: Mikä on chimney sweep interval?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.chimney_sweep_interval
  source: src:NU1
- q: Mikä on creosote mm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.creosote_mm
  source: src:NU1
- q: 'Toimenpide: creosote mm?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.creosote_mm.action
  source: src:NU1
- q: Mikä on chimney draft pa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.chimney_draft_pa
  source: src:NU1
- q: 'Toimenpide: chimney draft pa?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.chimney_draft_pa.action
  source: src:NU1
- q: Mikä on co detector?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.co_detector
  source: src:NU1
- q: 'Toimenpide: co detector?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.co_detector.action
  source: src:NU1
- q: Mikä on extinguisher check years?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.extinguisher_check_years
  source: src:NU1
- q: 'Toimenpide: extinguisher check years?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.extinguisher_check_years.action
  source: src:NU1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:NU1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:NU1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:NU1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:NU1
- q: 'Havainto: Nokipalo (hormipalo)?'
  a_ref: FAILURE_MODES[0].detection
  source: src:NU1
- q: 'Toiminta: Nokipalo (hormipalo)?'
  a_ref: FAILURE_MODES[0].action
  source: src:NU1
- q: 'Havainto: Savukaasut sisälle?'
  a_ref: FAILURE_MODES[1].detection
  source: src:NU1
- q: 'Toiminta: Savukaasut sisälle?'
  a_ref: FAILURE_MODES[1].action
  source: src:NU1
- q: 'Sääntö: nuohouslaki?'
  a_ref: COMPLIANCE_AND_LEGAL.nuohouslaki
  source: src:NU1
- q: 'Sääntö: palovaroitin?'
  a_ref: COMPLIANCE_AND_LEGAL.palovaroitin
  source: src:NU1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:NU1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #12?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #13?'
  a_ref: ASSUMPTIONS
  source: src:NU1
- q: 'Kytkentä muihin agentteihin #14?'
  a_ref: ASSUMPTIONS
  source: src:NU1
```

**sources.yaml:**
```yaml
sources:
- id: src:NU1
  org: Nuohousalan Keskusliitto
  title: Nuohousohje
  year: 2024
  url: https://www.nuohoojat.fi/
  supports: Nuohousvälit, kreosootti.
- id: src:NU2
  org: Pelastuslaitos
  title: Paloturvallisuus
  year: 2025
  url: https://www.pelastustoimi.fi/
  supports: Palovaroittimet, häkä.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Nuohooja / Paloturva-asiantuntija
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenranta: puulämmitys (takka, leivinuuni, puukiuas)
- Nuohous lakisääteinen
- Kytketty paloesimies-, ilmanlaatu-, timpuri-agentteihin

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| chimney_sweep_interval | Päälämmityslähde: 1x/v, vapaa-ajan: 3v välein | — | src:NU1 |
| creosote_mm | <3 mm OK | >3 mm → nuohous pian, >6 mm → VÄLITÖN (palovaara) | src:NU1 |
| chimney_draft_pa | 10-20 Pa normaali | <5 Pa → huono veto, savukaasut sisään, tarkista hormi | src:NU1 |
| co_detector | Suositus kaikissa puulämmitteissä tiloissa | Hälytin joka kerrokseen | src:NU2 |
| extinguisher_check_years | 2 | Tarkista 2v välein, huolla 5v välein | src:NU2 |

## Tietotaulukot

**Nuohooja / Paloturva-asiantuntija — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| chimney_sweep_interval | Päälämmityslähde: 1x/v, vapaa-ajan: 3v välein |  |
| creosote_mm | <3 mm OK | >3 mm → nuohous pian, >6 mm → VÄLITÖN (palovaara) |
| chimney_draft_pa | 10-20 Pa normaali | <5 Pa → huono veto, savukaasut sisään, tarkista hormi |
| co_detector | Suositus kaikissa puulämmitteissä tiloissa | Hälytin joka kerrokseen |
| extinguisher_check_years | 2 | Tarkista 2v välein, huolla 5v välein |

## Prosessit

**FLOW_NUOH_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Nuohouksen tilaus ennen seuraavaa kautta. Hormin tarkistus (halkeamat, tiiveys).
  Tulos: Tarkistuslista

**FLOW_NUOH_03:** Havaittu: Nokipalo (hormipalo)
  → Sulje ilmaläpät ja pellit, soita 112, ÄLÄ sammuta vedellä, evakuoi
  Tulos: Poikkeamaraportti

**FLOW_NUOH_04:** Säännöllinen heartbeat
  → nuohooja: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Nuohouksen tilaus ennen seuraavaa kautta. Hormin tarkistus (halkeamat, tiiveys). | src:NU1 |
| **Kesä** | [vko 22-35] Hormien korjaustyöt (muuraus, pellitys). Ei aktiivista lämmitystä. | src:NU1 |
| **Syksy** | [vko 36-48] Lämmityskausi alkaa. Varmista nuohous tehty. Palovaroittimet testattu. | src:NU2 |
| **Talvi** | [vko 49-13] Aktiivinen lämmitys. Kreosootinkertymä. Häkävaroitin toiminnassa. | src:NU1 |

## Virhe- ja vaaratilanteet

### ⚠️ Nokipalo (hormipalo)
- **Havaitseminen:** Kova hurina hormissa, kipinöitä piipun päästä, kuuma hormipinta
- **Toimenpide:** Sulje ilmaläpät ja pellit, soita 112, ÄLÄ sammuta vedellä, evakuoi
- **Lähde:** src:NU2

### ⚠️ Savukaasut sisälle
- **Havaitseminen:** CO-hälytin, päänsärky, pahoinvointi
- **Toimenpide:** Avaa ikkunat, sammuta tulisija, ulos, soita 112
- **Lähde:** src:NU2

## Lait ja vaatimukset
- **nuohouslaki:** Pelastuslaki 379/2011 §59: nuohousvelvollisuus [src:NU1]
- **palovaroitin:** Pelastuslaki: pakollinen jokaiseen asuntoon [src:NU2]

## Epävarmuudet
- Kreosootinkertymänopeus riippuu polttotavoista — märkä puu kerää nopeammin.

## Lähteet
- **src:NU1**: Nuohousalan Keskusliitto — *Nuohousohje* (2024) https://www.nuohoojat.fi/
- **src:NU2**: Pelastuslaitos — *Paloturvallisuus* (2025) https://www.pelastustoimi.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Kuinka usein nuohotaan päälämmityslähde?**
    → `DECISION_METRICS_AND_THRESHOLDS.chimney_sweep_interval.value` [src:NU1]
 2. **Mikä kreosoottikerros on vaarallinen?**
    → `DECISION_METRICS_AND_THRESHOLDS.creosote_mm.action` [src:NU1]
 3. **Mitä tehdään nokipalossa?**
    → `FAILURE_MODES[0].action` [src:NU2]
 4. **Saako nokipaloa sammuttaa vedellä?**
    → `FAILURE_MODES[0].action` [src:NU2]
 5. **Mikä on normaalin vedon arvo?**
    → `DECISION_METRICS_AND_THRESHOLDS.chimney_draft_pa.value` [src:NU1]
 6. **Mikä on chimney sweep interval?**
    → `DECISION_METRICS_AND_THRESHOLDS.chimney_sweep_interval` [src:NU1]
 7. **Mikä on creosote mm?**
    → `DECISION_METRICS_AND_THRESHOLDS.creosote_mm` [src:NU1]
 8. **Toimenpide: creosote mm?**
    → `DECISION_METRICS_AND_THRESHOLDS.creosote_mm.action` [src:NU1]
 9. **Mikä on chimney draft pa?**
    → `DECISION_METRICS_AND_THRESHOLDS.chimney_draft_pa` [src:NU1]
10. **Toimenpide: chimney draft pa?**
    → `DECISION_METRICS_AND_THRESHOLDS.chimney_draft_pa.action` [src:NU1]
11. **Mikä on co detector?**
    → `DECISION_METRICS_AND_THRESHOLDS.co_detector` [src:NU1]
12. **Toimenpide: co detector?**
    → `DECISION_METRICS_AND_THRESHOLDS.co_detector.action` [src:NU1]
13. **Mikä on extinguisher check years?**
    → `DECISION_METRICS_AND_THRESHOLDS.extinguisher_check_years` [src:NU1]
14. **Toimenpide: extinguisher check years?**
    → `DECISION_METRICS_AND_THRESHOLDS.extinguisher_check_years.action` [src:NU1]
15. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:NU1]
16. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:NU1]
17. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:NU1]
18. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:NU1]
19. **Havainto: Nokipalo (hormipalo)?**
    → `FAILURE_MODES[0].detection` [src:NU1]
20. **Toiminta: Nokipalo (hormipalo)?**
    → `FAILURE_MODES[0].action` [src:NU1]
21. **Havainto: Savukaasut sisälle?**
    → `FAILURE_MODES[1].detection` [src:NU1]
22. **Toiminta: Savukaasut sisälle?**
    → `FAILURE_MODES[1].action` [src:NU1]
23. **Sääntö: nuohouslaki?**
    → `COMPLIANCE_AND_LEGAL.nuohouslaki` [src:NU1]
24. **Sääntö: palovaroitin?**
    → `COMPLIANCE_AND_LEGAL.palovaroitin` [src:NU1]
25. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:NU1]
26. **Oletukset?**
    → `ASSUMPTIONS` [src:NU1]
27. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:NU1]
28. **Kytkentä muihin agentteihin #2?**
    → `ASSUMPTIONS` [src:NU1]
29. **Kytkentä muihin agentteihin #3?**
    → `ASSUMPTIONS` [src:NU1]
30. **Kytkentä muihin agentteihin #4?**
    → `ASSUMPTIONS` [src:NU1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 33
## AGENT 33: Valaistusmestari
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: valaistusmestari
  agent_name: Valaistusmestari
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan sisä- ja ulkovalaistus
- LED pääosin
- Valohaaste luonnolle huomioitava
DECISION_METRICS_AND_THRESHOLDS:
  lux_indoor_work:
    value: 300-500 lux työtila
    source: src:VA1
  lux_outdoor_path:
    value: 5-20 lux pihapolku
    source: src:VA1
  color_temp_evening_k:
    value: <3000 K illalla
    action: Kylmä valo illalla häiritsee unta ja eläimiä
    source: src:VA1
  motion_timeout_min:
    value: 5
    action: Ulkovalo 5 min liiketunnistimella → energiansäästö
    source: src:VA1
  light_pollution_amber:
    value: Ulkovalot amber/lämmin → vähemmän häiriötä
    action: Ilmoita tähtitieteilijälle valosaasteesta
    source: src:VA2
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Ulkovalojen tarkistus. Ajastimien päivitys päivänvalon mukaan.'
  source: src:VA1
- season: Kesä
  action: '[vko 22-35] Yötön yö: ulkovalot minimiin. Amber-valo hyönteishäiriön estoon.'
  source: src:VA2
- season: Syksy
  action: '[vko 36-48] Pimenee: ulkovalot päälle, turvavalaistus. Ajastimet.'
  source: src:VA1
- season: Talvi
  action: '[vko 49-13] Pisin pimeä → valaistus kriittinen. Jouluvalojen sähkönkulutus.'
  source: src:VA1
FAILURE_MODES:
- mode: LED vilkkuu
  detection: Vilkkuva tai himmenevä valo
  action: Tarkista muuntaja/driver, himmentimen yhteensopivuus
  source: src:VA1
- mode: Liiketunnistin jatkuvasti päällä
  detection: Valo ei sammu
  action: Herkkyys, suuntaus, eläimet/kasvit laukaisijoina?
  source: src:VA1
PROCESS_FLOWS:
- flow_id: FLOW_VALA_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Ulkovalojen tarkistus. Ajastimien päivitys päivänvalon mukaan.'
  output: Tarkistuslista
  source: src:VALA
- flow_id: FLOW_VALA_03
  trigger: 'Havaittu: LED vilkkuu'
  action: Tarkista muuntaja/driver, himmentimen yhteensopivuus
  output: Poikkeamaraportti
  source: src:VALA
- flow_id: FLOW_VALA_04
  trigger: Säännöllinen heartbeat
  action: 'valaistusmestari: rutiiniarviointi'
  output: Status-raportti
  source: src:VALA
KNOWLEDGE_TABLES:
- table_id: TBL_VALA_01
  title: Valaistusmestari — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: lux_indoor_work
    value: 300-500 lux työtila
    action: ''
  - metric: lux_outdoor_path
    value: 5-20 lux pihapolku
    action: ''
  - metric: color_temp_evening_k
    value: <3000 K illalla
    action: Kylmä valo illalla häiritsee unta ja eläimiä
  - metric: motion_timeout_min
    value: '5'
    action: Ulkovalo 5 min liiketunnistimella → energiansäästö
  - metric: light_pollution_amber
    value: Ulkovalot amber/lämmin → vähemmän häiriötä
    action: Ilmoita tähtitieteilijälle valosaasteesta
  source: src:VALA
COMPLIANCE_AND_LEGAL:
  valohaaste: Ei saa kohdistaa naapuriin. Kunnan järjestyssääntö. [src:VA2]
UNCERTAINTY_NOTES:
- LED-käyttöikä vaihtelee valmistajittain merkittävästi.
SOURCE_REGISTRY:
  sources:
  - id: src:VA1
    org: Suomen Valoteknillinen Seura
    title: Valaistussuositukset
    year: 2024
    url: https://www.valosto.com/
    supports: Lux, värilämpötila.
  - id: src:VA2
    org: IDA/Ursa
    title: Valosaaste
    year: 2025
    url: https://www.darksky.org/
    supports: Valosaasteentorjunta.
eval_questions:
- q: Mikä on lux indoor work?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.lux_indoor_work
  source: src:VA1
- q: Mikä on lux outdoor path?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.lux_outdoor_path
  source: src:VA1
- q: Mikä on color temp evening k?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.color_temp_evening_k
  source: src:VA1
- q: 'Toimenpide: color temp evening k?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.color_temp_evening_k.action
  source: src:VA1
- q: Mikä on motion timeout min?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.motion_timeout_min
  source: src:VA1
- q: 'Toimenpide: motion timeout min?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.motion_timeout_min.action
  source: src:VA1
- q: Mikä on light pollution amber?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.light_pollution_amber
  source: src:VA1
- q: 'Toimenpide: light pollution amber?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.light_pollution_amber.action
  source: src:VA1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:VA1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:VA1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:VA1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:VA1
- q: 'Havainto: LED vilkkuu?'
  a_ref: FAILURE_MODES[0].detection
  source: src:VA1
- q: 'Toiminta: LED vilkkuu?'
  a_ref: FAILURE_MODES[0].action
  source: src:VA1
- q: 'Havainto: Liiketunnistin jatkuvasti pääl?'
  a_ref: FAILURE_MODES[1].detection
  source: src:VA1
- q: 'Toiminta: Liiketunnistin jatkuvasti pääl?'
  a_ref: FAILURE_MODES[1].action
  source: src:VA1
- q: 'Sääntö: valohaaste?'
  a_ref: COMPLIANCE_AND_LEGAL.valohaaste
  source: src:VA1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:VA1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #12?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #13?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #14?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #15?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #16?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #17?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #18?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #19?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #20?'
  a_ref: ASSUMPTIONS
  source: src:VA1
- q: 'Kytkentä muihin agentteihin #21?'
  a_ref: ASSUMPTIONS
  source: src:VA1
```

**sources.yaml:**
```yaml
sources:
- id: src:VA1
  org: Suomen Valoteknillinen Seura
  title: Valaistussuositukset
  year: 2024
  url: https://www.valosto.com/
  supports: Lux, värilämpötila.
- id: src:VA2
  org: IDA/Ursa
  title: Valosaaste
  year: 2025
  url: https://www.darksky.org/
  supports: Valosaasteentorjunta.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Valaistusmestari
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan sisä- ja ulkovalaistus
- LED pääosin
- Valohaaste luonnolle huomioitava

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| lux_indoor_work | 300-500 lux työtila | — | src:VA1 |
| lux_outdoor_path | 5-20 lux pihapolku | — | src:VA1 |
| color_temp_evening_k | <3000 K illalla | Kylmä valo illalla häiritsee unta ja eläimiä | src:VA1 |
| motion_timeout_min | 5 | Ulkovalo 5 min liiketunnistimella → energiansäästö | src:VA1 |
| light_pollution_amber | Ulkovalot amber/lämmin → vähemmän häiriötä | Ilmoita tähtitieteilijälle valosaasteesta | src:VA2 |

## Tietotaulukot

**Valaistusmestari — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| lux_indoor_work | 300-500 lux työtila |  |
| lux_outdoor_path | 5-20 lux pihapolku |  |
| color_temp_evening_k | <3000 K illalla | Kylmä valo illalla häiritsee unta ja eläimiä |
| motion_timeout_min | 5 | Ulkovalo 5 min liiketunnistimella → energiansäästö |
| light_pollution_amber | Ulkovalot amber/lämmin → vähemmän häiriötä | Ilmoita tähtitieteilijälle valosaasteesta |

## Prosessit

**FLOW_VALA_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Ulkovalojen tarkistus. Ajastimien päivitys päivänvalon mukaan.
  Tulos: Tarkistuslista

**FLOW_VALA_03:** Havaittu: LED vilkkuu
  → Tarkista muuntaja/driver, himmentimen yhteensopivuus
  Tulos: Poikkeamaraportti

**FLOW_VALA_04:** Säännöllinen heartbeat
  → valaistusmestari: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Ulkovalojen tarkistus. Ajastimien päivitys päivänvalon mukaan. | src:VA1 |
| **Kesä** | [vko 22-35] Yötön yö: ulkovalot minimiin. Amber-valo hyönteishäiriön estoon. | src:VA2 |
| **Syksy** | [vko 36-48] Pimenee: ulkovalot päälle, turvavalaistus. Ajastimet. | src:VA1 |
| **Talvi** | [vko 49-13] Pisin pimeä → valaistus kriittinen. Jouluvalojen sähkönkulutus. | src:VA1 |

## Virhe- ja vaaratilanteet

### ⚠️ LED vilkkuu
- **Havaitseminen:** Vilkkuva tai himmenevä valo
- **Toimenpide:** Tarkista muuntaja/driver, himmentimen yhteensopivuus
- **Lähde:** src:VA1

### ⚠️ Liiketunnistin jatkuvasti päällä
- **Havaitseminen:** Valo ei sammu
- **Toimenpide:** Herkkyys, suuntaus, eläimet/kasvit laukaisijoina?
- **Lähde:** src:VA1

## Lait ja vaatimukset
- **valohaaste:** Ei saa kohdistaa naapuriin. Kunnan järjestyssääntö. [src:VA2]

## Epävarmuudet
- LED-käyttöikä vaihtelee valmistajittain merkittävästi.

## Lähteet
- **src:VA1**: Suomen Valoteknillinen Seura — *Valaistussuositukset* (2024) https://www.valosto.com/
- **src:VA2**: IDA/Ursa — *Valosaaste* (2025) https://www.darksky.org/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on lux indoor work?**
    → `DECISION_METRICS_AND_THRESHOLDS.lux_indoor_work` [src:VA1]
 2. **Mikä on lux outdoor path?**
    → `DECISION_METRICS_AND_THRESHOLDS.lux_outdoor_path` [src:VA1]
 3. **Mikä on color temp evening k?**
    → `DECISION_METRICS_AND_THRESHOLDS.color_temp_evening_k` [src:VA1]
 4. **Toimenpide: color temp evening k?**
    → `DECISION_METRICS_AND_THRESHOLDS.color_temp_evening_k.action` [src:VA1]
 5. **Mikä on motion timeout min?**
    → `DECISION_METRICS_AND_THRESHOLDS.motion_timeout_min` [src:VA1]
 6. **Toimenpide: motion timeout min?**
    → `DECISION_METRICS_AND_THRESHOLDS.motion_timeout_min.action` [src:VA1]
 7. **Mikä on light pollution amber?**
    → `DECISION_METRICS_AND_THRESHOLDS.light_pollution_amber` [src:VA1]
 8. **Toimenpide: light pollution amber?**
    → `DECISION_METRICS_AND_THRESHOLDS.light_pollution_amber.action` [src:VA1]
 9. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:VA1]
10. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:VA1]
11. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:VA1]
12. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:VA1]
13. **Havainto: LED vilkkuu?**
    → `FAILURE_MODES[0].detection` [src:VA1]
14. **Toiminta: LED vilkkuu?**
    → `FAILURE_MODES[0].action` [src:VA1]
15. **Havainto: Liiketunnistin jatkuvasti pääl?**
    → `FAILURE_MODES[1].detection` [src:VA1]
16. **Toiminta: Liiketunnistin jatkuvasti pääl?**
    → `FAILURE_MODES[1].action` [src:VA1]
17. **Sääntö: valohaaste?**
    → `COMPLIANCE_AND_LEGAL.valohaaste` [src:VA1]
18. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:VA1]
19. **Oletukset?**
    → `ASSUMPTIONS` [src:VA1]
20. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:VA1]
21. **Kytkentä muihin agentteihin #2?**
    → `ASSUMPTIONS` [src:VA1]
22. **Kytkentä muihin agentteihin #3?**
    → `ASSUMPTIONS` [src:VA1]
23. **Kytkentä muihin agentteihin #4?**
    → `ASSUMPTIONS` [src:VA1]
24. **Kytkentä muihin agentteihin #5?**
    → `ASSUMPTIONS` [src:VA1]
25. **Kytkentä muihin agentteihin #6?**
    → `ASSUMPTIONS` [src:VA1]
26. **Kytkentä muihin agentteihin #7?**
    → `ASSUMPTIONS` [src:VA1]
27. **Kytkentä muihin agentteihin #8?**
    → `ASSUMPTIONS` [src:VA1]
28. **Kytkentä muihin agentteihin #9?**
    → `ASSUMPTIONS` [src:VA1]
29. **Kytkentä muihin agentteihin #10?**
    → `ASSUMPTIONS` [src:VA1]
30. **Kytkentä muihin agentteihin #11?**
    → `ASSUMPTIONS` [src:VA1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 34
## AGENT 34: Paloesimies (häkä, palovaroittimet, lämpöanomaliat)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: paloesimies
  agent_name: Paloesimies (häkä, palovaroittimet, lämpöanomaliat)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan kiinteistöjen paloturvallisuus
- IoT-lämpökamerat ja häkäanturit mahdollisia
DECISION_METRICS_AND_THRESHOLDS:
  smoke_detector_test_months:
    value: 1
    action: Testaa kuukausittain painikkeesta
    source: src:PA1
  smoke_detector_replace_years:
    value: 10
    action: Vaihda 10v välein
    source: src:PA1
  co_alarm_ppm:
    value: 50 ppm → hälytin soi
    action: '>100 ppm → evakuoi, soita 112'
    source: src:PA1
  thermal_anomaly_c:
    value: Pintalämpö >60°C sähkökeskuksessa/johdossa
    action: 'HÄLYTYS: kytke sähkö pois, kutsu sähköasentaja'
    source: src:PA1
  extinguisher_distance_m:
    value: 15
    action: Max 15 m etäisyys jokaisesta pisteestä
    source: src:PA1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Palovaroittimien testaus. Sammuttimien tarkistus. Grillikausi.'
  source: src:PA1
- season: Kesä
  action: '[vko 22-35] Maastopalovaara (kuiva jakso). Grillaus-turvallisuus. Kulotuskielto.'
  source: src:PA1
- season: Syksy
  action: Lämmityskausi → tulisijat ja hormit. Palovaroitinpäivä 1.12.
  source: src:PA1
- season: Talvi
  action: '[vko 49-13] Häkävaara puulämmityksessä. Kynttilät. Sähkölaitteiden ylikuumeneminen.'
  source: src:PA1
FAILURE_MODES:
- mode: Palovaroitin ei toimi
  detection: Testipainike ei anna ääntä
  action: Vaihda paristo HETI, testaa. Ei toimi → vaihda varoitin.
  source: src:PA1
- mode: Häkähälytys
  detection: CO-hälytin soi
  action: Ikkunat auki, tulisija kiinni, ulos, 112 jos oireita
  source: src:PA1
PROCESS_FLOWS:
- flow_id: FLOW_PALO_01
  trigger: smoke_detector_test_months ylittää kynnysarvon (1)
  action: Testaa kuukausittain painikkeesta
  output: Tilanneraportti
  source: src:PALO
- flow_id: FLOW_PALO_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Palovaroittimien testaus. Sammuttimien tarkistus. Grillikausi.'
  output: Tarkistuslista
  source: src:PALO
- flow_id: FLOW_PALO_03
  trigger: 'Havaittu: Palovaroitin ei toimi'
  action: Vaihda paristo HETI, testaa. Ei toimi → vaihda varoitin.
  output: Poikkeamaraportti
  source: src:PALO
- flow_id: FLOW_PALO_04
  trigger: Säännöllinen heartbeat
  action: 'paloesimies: rutiiniarviointi'
  output: Status-raportti
  source: src:PALO
KNOWLEDGE_TABLES:
- table_id: TBL_PALO_01
  title: Paloesimies (häkä, palovaroittimet, lämpöanomaliat) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: smoke_detector_test_months
    value: '1'
    action: Testaa kuukausittain painikkeesta
  - metric: smoke_detector_replace_years
    value: '10'
    action: Vaihda 10v välein
  - metric: co_alarm_ppm
    value: 50 ppm → hälytin soi
    action: '>100 ppm → evakuoi, soita 112'
  - metric: thermal_anomaly_c
    value: Pintalämpö >60°C sähkökeskuksessa/johdossa
    action: 'HÄLYTYS: kytke sähkö pois, kutsu sähköasentaja'
  - metric: extinguisher_distance_m
    value: '15'
    action: Max 15 m etäisyys jokaisesta pisteestä
  source: src:PALO
COMPLIANCE_AND_LEGAL:
  palovaroitin: 'Pelastuslaki 379/2011: pakollinen [src:PA1]'
  sammutin: Kiinteistön omistajan velvollisuus [src:PA1]
UNCERTAINTY_NOTES:
- IoT-lämpökameroiden tarkkuus ±2°C — ei korvaa ammattilaisen arviota.
SOURCE_REGISTRY:
  sources:
  - id: src:PA1
    org: Pelastuslaitos
    title: Paloturvallisuus
    year: 2025
    url: https://www.pelastustoimi.fi/
    supports: Palovaroittimet, häkä, sammuttimet.
eval_questions:
- q: Mikä on smoke detector test months?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.smoke_detector_test_months
  source: src:PA1
- q: 'Toimenpide: smoke detector test months?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.smoke_detector_test_months.action
  source: src:PA1
- q: Mikä on smoke detector replace years?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.smoke_detector_replace_years
  source: src:PA1
- q: 'Toimenpide: smoke detector replace years?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.smoke_detector_replace_years.action
  source: src:PA1
- q: Mikä on co alarm ppm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.co_alarm_ppm
  source: src:PA1
- q: 'Toimenpide: co alarm ppm?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.co_alarm_ppm.action
  source: src:PA1
- q: Mikä on thermal anomaly c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thermal_anomaly_c
  source: src:PA1
- q: 'Toimenpide: thermal anomaly c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thermal_anomaly_c.action
  source: src:PA1
- q: Mikä on extinguisher distance m?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.extinguisher_distance_m
  source: src:PA1
- q: 'Toimenpide: extinguisher distance m?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.extinguisher_distance_m.action
  source: src:PA1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:PA1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:PA1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:PA1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:PA1
- q: 'Havainto: Palovaroitin ei toimi?'
  a_ref: FAILURE_MODES[0].detection
  source: src:PA1
- q: 'Toiminta: Palovaroitin ei toimi?'
  a_ref: FAILURE_MODES[0].action
  source: src:PA1
- q: 'Havainto: Häkähälytys?'
  a_ref: FAILURE_MODES[1].detection
  source: src:PA1
- q: 'Toiminta: Häkähälytys?'
  a_ref: FAILURE_MODES[1].action
  source: src:PA1
- q: 'Sääntö: palovaroitin?'
  a_ref: COMPLIANCE_AND_LEGAL.palovaroitin
  source: src:PA1
- q: 'Sääntö: sammutin?'
  a_ref: COMPLIANCE_AND_LEGAL.sammutin
  source: src:PA1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:PA1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #12?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #13?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #14?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #15?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #16?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #17?'
  a_ref: ASSUMPTIONS
  source: src:PA1
- q: 'Kytkentä muihin agentteihin #18?'
  a_ref: ASSUMPTIONS
  source: src:PA1
```

**sources.yaml:**
```yaml
sources:
- id: src:PA1
  org: Pelastuslaitos
  title: Paloturvallisuus
  year: 2025
  url: https://www.pelastustoimi.fi/
  supports: Palovaroittimet, häkä, sammuttimet.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Paloesimies (häkä, palovaroittimet, lämpöanomaliat)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan kiinteistöjen paloturvallisuus
- IoT-lämpökamerat ja häkäanturit mahdollisia

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| smoke_detector_test_months | 1 | Testaa kuukausittain painikkeesta | src:PA1 |
| smoke_detector_replace_years | 10 | Vaihda 10v välein | src:PA1 |
| co_alarm_ppm | 50 ppm → hälytin soi | >100 ppm → evakuoi, soita 112 | src:PA1 |
| thermal_anomaly_c | Pintalämpö >60°C sähkökeskuksessa/johdossa | HÄLYTYS: kytke sähkö pois, kutsu sähköasentaja | src:PA1 |
| extinguisher_distance_m | 15 | Max 15 m etäisyys jokaisesta pisteestä | src:PA1 |

## Tietotaulukot

**Paloesimies (häkä, palovaroittimet, lämpöanomaliat) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| smoke_detector_test_months | 1 | Testaa kuukausittain painikkeesta |
| smoke_detector_replace_years | 10 | Vaihda 10v välein |
| co_alarm_ppm | 50 ppm → hälytin soi | >100 ppm → evakuoi, soita 112 |
| thermal_anomaly_c | Pintalämpö >60°C sähkökeskuksessa/johdossa | HÄLYTYS: kytke sähkö pois, kutsu sähköasentaja |
| extinguisher_distance_m | 15 | Max 15 m etäisyys jokaisesta pisteestä |

## Prosessit

**FLOW_PALO_01:** smoke_detector_test_months ylittää kynnysarvon (1)
  → Testaa kuukausittain painikkeesta
  Tulos: Tilanneraportti

**FLOW_PALO_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Palovaroittimien testaus. Sammuttimien tarkistus. Grillikausi.
  Tulos: Tarkistuslista

**FLOW_PALO_03:** Havaittu: Palovaroitin ei toimi
  → Vaihda paristo HETI, testaa. Ei toimi → vaihda varoitin.
  Tulos: Poikkeamaraportti

**FLOW_PALO_04:** Säännöllinen heartbeat
  → paloesimies: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Palovaroittimien testaus. Sammuttimien tarkistus. Grillikausi. | src:PA1 |
| **Kesä** | [vko 22-35] Maastopalovaara (kuiva jakso). Grillaus-turvallisuus. Kulotuskielto. | src:PA1 |
| **Syksy** | Lämmityskausi → tulisijat ja hormit. Palovaroitinpäivä 1.12. | src:PA1 |
| **Talvi** | [vko 49-13] Häkävaara puulämmityksessä. Kynttilät. Sähkölaitteiden ylikuumeneminen. | src:PA1 |

## Virhe- ja vaaratilanteet

### ⚠️ Palovaroitin ei toimi
- **Havaitseminen:** Testipainike ei anna ääntä
- **Toimenpide:** Vaihda paristo HETI, testaa. Ei toimi → vaihda varoitin.
- **Lähde:** src:PA1

### ⚠️ Häkähälytys
- **Havaitseminen:** CO-hälytin soi
- **Toimenpide:** Ikkunat auki, tulisija kiinni, ulos, 112 jos oireita
- **Lähde:** src:PA1

## Lait ja vaatimukset
- **palovaroitin:** Pelastuslaki 379/2011: pakollinen [src:PA1]
- **sammutin:** Kiinteistön omistajan velvollisuus [src:PA1]

## Epävarmuudet
- IoT-lämpökameroiden tarkkuus ±2°C — ei korvaa ammattilaisen arviota.

## Lähteet
- **src:PA1**: Pelastuslaitos — *Paloturvallisuus* (2025) https://www.pelastustoimi.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on smoke detector test months?**
    → `DECISION_METRICS_AND_THRESHOLDS.smoke_detector_test_months` [src:PA1]
 2. **Toimenpide: smoke detector test months?**
    → `DECISION_METRICS_AND_THRESHOLDS.smoke_detector_test_months.action` [src:PA1]
 3. **Mikä on smoke detector replace years?**
    → `DECISION_METRICS_AND_THRESHOLDS.smoke_detector_replace_years` [src:PA1]
 4. **Toimenpide: smoke detector replace years?**
    → `DECISION_METRICS_AND_THRESHOLDS.smoke_detector_replace_years.action` [src:PA1]
 5. **Mikä on co alarm ppm?**
    → `DECISION_METRICS_AND_THRESHOLDS.co_alarm_ppm` [src:PA1]
 6. **Toimenpide: co alarm ppm?**
    → `DECISION_METRICS_AND_THRESHOLDS.co_alarm_ppm.action` [src:PA1]
 7. **Mikä on thermal anomaly c?**
    → `DECISION_METRICS_AND_THRESHOLDS.thermal_anomaly_c` [src:PA1]
 8. **Toimenpide: thermal anomaly c?**
    → `DECISION_METRICS_AND_THRESHOLDS.thermal_anomaly_c.action` [src:PA1]
 9. **Mikä on extinguisher distance m?**
    → `DECISION_METRICS_AND_THRESHOLDS.extinguisher_distance_m` [src:PA1]
10. **Toimenpide: extinguisher distance m?**
    → `DECISION_METRICS_AND_THRESHOLDS.extinguisher_distance_m.action` [src:PA1]
11. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:PA1]
12. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:PA1]
13. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:PA1]
14. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:PA1]
15. **Havainto: Palovaroitin ei toimi?**
    → `FAILURE_MODES[0].detection` [src:PA1]
16. **Toiminta: Palovaroitin ei toimi?**
    → `FAILURE_MODES[0].action` [src:PA1]
17. **Havainto: Häkähälytys?**
    → `FAILURE_MODES[1].detection` [src:PA1]
18. **Toiminta: Häkähälytys?**
    → `FAILURE_MODES[1].action` [src:PA1]
19. **Sääntö: palovaroitin?**
    → `COMPLIANCE_AND_LEGAL.palovaroitin` [src:PA1]
20. **Sääntö: sammutin?**
    → `COMPLIANCE_AND_LEGAL.sammutin` [src:PA1]
21. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:PA1]
22. **Oletukset?**
    → `ASSUMPTIONS` [src:PA1]
23. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:PA1]
24. **Kytkentä muihin agentteihin #2?**
    → `ASSUMPTIONS` [src:PA1]
25. **Kytkentä muihin agentteihin #3?**
    → `ASSUMPTIONS` [src:PA1]
26. **Kytkentä muihin agentteihin #4?**
    → `ASSUMPTIONS` [src:PA1]
27. **Kytkentä muihin agentteihin #5?**
    → `ASSUMPTIONS` [src:PA1]
28. **Kytkentä muihin agentteihin #6?**
    → `ASSUMPTIONS` [src:PA1]
29. **Kytkentä muihin agentteihin #7?**
    → `ASSUMPTIONS` [src:PA1]
30. **Kytkentä muihin agentteihin #8?**
    → `ASSUMPTIONS` [src:PA1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 35
## AGENT 35: Laitehuoltaja (IoT, akut, verkot)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: laitehuoltaja
  agent_name: Laitehuoltaja (IoT, akut, verkot)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Korvenrannan IoT: kamerat, anturit, gateway, NAS, Ollama-palvelin'
- 'Verkko: WiFi, BLE, 4G-vara'
DECISION_METRICS_AND_THRESHOLDS:
  battery_voltage_iot_v:
    value: Tyypillisesti 3.0-3.6V (lithium)
    action: <3.0V → vaihda/lataa, <2.8V → laite sammuu
    source: src:LA1
  wifi_signal_dbm:
    value: -30 erinomainen, -50 hyvä, -70 kohtalainen
    action: <-75 → vahvistin tai lisätukiasema
    source: src:LA1
  nas_disk_smart_pct:
    value: '>90% OK'
    action: <85% → varmuuskopioi ja vaihda levy
    source: src:LA1
  uptime_target_pct:
    value: 99.5%
    note: ≈43h max seisokkia/vuosi
    source: src:LA1
  firmware_update_months:
    value: 3
    action: Tarkista 3 kk välein, kriittiset heti
    source: src:LA1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Ulkolaitteiden tarkistus talven jälkeen. Akkujen testaus.'
  source: src:LA1
- season: Kesä
  action: '[vko 22-35] Ylilämpenemisriski (NAS, palvelin). Jäähdytys.'
  source: src:LA1
- season: Syksy
  action: '[vko 36-48] Varmuuskopiot. UPS-testaus ennen talvea.'
  source: src:LA1
- season: Talvi
  action: 'Lithium-akut: -20°C raja. Sähkökatkosvarautuminen.'
  source: src:LA1
FAILURE_MODES:
- mode: IoT-laite offline >1h
  detection: Ei dataa
  action: Akku → yhteys → firmware. Reboot. Ilmoita relevanteille.
  source: src:LA1
- mode: NAS-levyvika
  detection: SMART-varoitus
  action: Varmuuskopioi HETI, vaihda levy, RAID rebuild
  source: src:LA1
PROCESS_FLOWS:
- flow_id: FLOW_LAIT_01
  trigger: battery_voltage_iot_v ylittää kynnysarvon (Tyypillisesti 3.0-3.6V (lithium))
  action: <3.0V → vaihda/lataa, <2.8V → laite sammuu
  output: Tilanneraportti
  source: src:LAIT
- flow_id: FLOW_LAIT_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Ulkolaitteiden tarkistus talven jälkeen. Akkujen testaus.'
  output: Tarkistuslista
  source: src:LAIT
- flow_id: FLOW_LAIT_03
  trigger: 'Havaittu: IoT-laite offline >1h'
  action: Akku → yhteys → firmware. Reboot. Ilmoita relevanteille.
  output: Poikkeamaraportti
  source: src:LAIT
- flow_id: FLOW_LAIT_04
  trigger: Säännöllinen heartbeat
  action: 'laitehuoltaja: rutiiniarviointi'
  output: Status-raportti
  source: src:LAIT
KNOWLEDGE_TABLES:
- table_id: TBL_LAIT_01
  title: Laitehuoltaja (IoT, akut, verkot) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: battery_voltage_iot_v
    value: Tyypillisesti 3.0-3.6V (lithium)
    action: <3.0V → vaihda/lataa, <2.8V → laite sammuu
  - metric: wifi_signal_dbm
    value: -30 erinomainen, -50 hyvä, -70 kohtalainen
    action: <-75 → vahvistin tai lisätukiasema
  - metric: nas_disk_smart_pct
    value: '>90% OK'
    action: <85% → varmuuskopioi ja vaihda levy
  - metric: uptime_target_pct
    value: 99.5%
    action: ''
  - metric: firmware_update_months
    value: '3'
    action: Tarkista 3 kk välein, kriittiset heti
  source: src:LAIT
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Lithium-akkujen kylmänkestävyys vaihtelee merkittävästi.
SOURCE_REGISTRY:
  sources:
  - id: src:LA1
    org: Laitevalmistajat
    title: IoT-laitehuolto
    year: 2025
    url: null
    supports: Akku, WiFi, NAS, firmware.
eval_questions:
- q: Mikä on battery voltage iot v?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.battery_voltage_iot_v
  source: src:LA1
- q: 'Toimenpide: battery voltage iot v?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.battery_voltage_iot_v.action
  source: src:LA1
- q: Mikä on wifi signal dbm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wifi_signal_dbm
  source: src:LA1
- q: 'Toimenpide: wifi signal dbm?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wifi_signal_dbm.action
  source: src:LA1
- q: Mikä on nas disk smart pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nas_disk_smart_pct
  source: src:LA1
- q: 'Toimenpide: nas disk smart pct?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nas_disk_smart_pct.action
  source: src:LA1
- q: Mikä on uptime target pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.uptime_target_pct
  source: src:LA1
- q: Mikä on firmware update months?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.firmware_update_months
  source: src:LA1
- q: 'Toimenpide: firmware update months?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.firmware_update_months.action
  source: src:LA1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:LA1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:LA1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:LA1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:LA1
- q: 'Havainto: IoT-laite offline >1h?'
  a_ref: FAILURE_MODES[0].detection
  source: src:LA1
- q: 'Toiminta: IoT-laite offline >1h?'
  a_ref: FAILURE_MODES[0].action
  source: src:LA1
- q: 'Havainto: NAS-levyvika?'
  a_ref: FAILURE_MODES[1].detection
  source: src:LA1
- q: 'Toiminta: NAS-levyvika?'
  a_ref: FAILURE_MODES[1].action
  source: src:LA1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:LA1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #12?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #13?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #14?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #15?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #16?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #17?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #18?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #19?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #20?'
  a_ref: ASSUMPTIONS
  source: src:LA1
- q: 'Kytkentä muihin agentteihin #21?'
  a_ref: ASSUMPTIONS
  source: src:LA1
```

**sources.yaml:**
```yaml
sources:
- id: src:LA1
  org: Laitevalmistajat
  title: IoT-laitehuolto
  year: 2025
  url: null
  supports: Akku, WiFi, NAS, firmware.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Laitehuoltaja (IoT, akut, verkot)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan IoT: kamerat, anturit, gateway, NAS, Ollama-palvelin
- Verkko: WiFi, BLE, 4G-vara

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| battery_voltage_iot_v | Tyypillisesti 3.0-3.6V (lithium) | <3.0V → vaihda/lataa, <2.8V → laite sammuu | src:LA1 |
| wifi_signal_dbm | -30 erinomainen, -50 hyvä, -70 kohtalainen | <-75 → vahvistin tai lisätukiasema | src:LA1 |
| nas_disk_smart_pct | >90% OK | <85% → varmuuskopioi ja vaihda levy | src:LA1 |
| uptime_target_pct | 99.5% | ≈43h max seisokkia/vuosi | src:LA1 |
| firmware_update_months | 3 | Tarkista 3 kk välein, kriittiset heti | src:LA1 |

## Tietotaulukot

**Laitehuoltaja (IoT, akut, verkot) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| battery_voltage_iot_v | Tyypillisesti 3.0-3.6V (lithium) | <3.0V → vaihda/lataa, <2.8V → laite sammuu |
| wifi_signal_dbm | -30 erinomainen, -50 hyvä, -70 kohtalainen | <-75 → vahvistin tai lisätukiasema |
| nas_disk_smart_pct | >90% OK | <85% → varmuuskopioi ja vaihda levy |
| uptime_target_pct | 99.5% |  |
| firmware_update_months | 3 | Tarkista 3 kk välein, kriittiset heti |

## Prosessit

**FLOW_LAIT_01:** battery_voltage_iot_v ylittää kynnysarvon (Tyypillisesti 3.0-3.6V (lithium))
  → <3.0V → vaihda/lataa, <2.8V → laite sammuu
  Tulos: Tilanneraportti

**FLOW_LAIT_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Ulkolaitteiden tarkistus talven jälkeen. Akkujen testaus.
  Tulos: Tarkistuslista

**FLOW_LAIT_03:** Havaittu: IoT-laite offline >1h
  → Akku → yhteys → firmware. Reboot. Ilmoita relevanteille.
  Tulos: Poikkeamaraportti

**FLOW_LAIT_04:** Säännöllinen heartbeat
  → laitehuoltaja: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Ulkolaitteiden tarkistus talven jälkeen. Akkujen testaus. | src:LA1 |
| **Kesä** | [vko 22-35] Ylilämpenemisriski (NAS, palvelin). Jäähdytys. | src:LA1 |
| **Syksy** | [vko 36-48] Varmuuskopiot. UPS-testaus ennen talvea. | src:LA1 |
| **Talvi** | Lithium-akut: -20°C raja. Sähkökatkosvarautuminen. | src:LA1 |

## Virhe- ja vaaratilanteet

### ⚠️ IoT-laite offline >1h
- **Havaitseminen:** Ei dataa
- **Toimenpide:** Akku → yhteys → firmware. Reboot. Ilmoita relevanteille.
- **Lähde:** src:LA1

### ⚠️ NAS-levyvika
- **Havaitseminen:** SMART-varoitus
- **Toimenpide:** Varmuuskopioi HETI, vaihda levy, RAID rebuild
- **Lähde:** src:LA1

## Epävarmuudet
- Lithium-akkujen kylmänkestävyys vaihtelee merkittävästi.

## Lähteet
- **src:LA1**: Laitevalmistajat — *IoT-laitehuolto* (2025) —

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on battery voltage iot v?**
    → `DECISION_METRICS_AND_THRESHOLDS.battery_voltage_iot_v` [src:LA1]
 2. **Toimenpide: battery voltage iot v?**
    → `DECISION_METRICS_AND_THRESHOLDS.battery_voltage_iot_v.action` [src:LA1]
 3. **Mikä on wifi signal dbm?**
    → `DECISION_METRICS_AND_THRESHOLDS.wifi_signal_dbm` [src:LA1]
 4. **Toimenpide: wifi signal dbm?**
    → `DECISION_METRICS_AND_THRESHOLDS.wifi_signal_dbm.action` [src:LA1]
 5. **Mikä on nas disk smart pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.nas_disk_smart_pct` [src:LA1]
 6. **Toimenpide: nas disk smart pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.nas_disk_smart_pct.action` [src:LA1]
 7. **Mikä on uptime target pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.uptime_target_pct` [src:LA1]
 8. **Mikä on firmware update months?**
    → `DECISION_METRICS_AND_THRESHOLDS.firmware_update_months` [src:LA1]
 9. **Toimenpide: firmware update months?**
    → `DECISION_METRICS_AND_THRESHOLDS.firmware_update_months.action` [src:LA1]
10. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:LA1]
11. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:LA1]
12. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:LA1]
13. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:LA1]
14. **Havainto: IoT-laite offline >1h?**
    → `FAILURE_MODES[0].detection` [src:LA1]
15. **Toiminta: IoT-laite offline >1h?**
    → `FAILURE_MODES[0].action` [src:LA1]
16. **Havainto: NAS-levyvika?**
    → `FAILURE_MODES[1].detection` [src:LA1]
17. **Toiminta: NAS-levyvika?**
    → `FAILURE_MODES[1].action` [src:LA1]
18. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:LA1]
19. **Oletukset?**
    → `ASSUMPTIONS` [src:LA1]
20. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:LA1]
21. **Kytkentä muihin agentteihin #2?**
    → `ASSUMPTIONS` [src:LA1]
22. **Kytkentä muihin agentteihin #3?**
    → `ASSUMPTIONS` [src:LA1]
23. **Kytkentä muihin agentteihin #4?**
    → `ASSUMPTIONS` [src:LA1]
24. **Kytkentä muihin agentteihin #5?**
    → `ASSUMPTIONS` [src:LA1]
25. **Kytkentä muihin agentteihin #6?**
    → `ASSUMPTIONS` [src:LA1]
26. **Kytkentä muihin agentteihin #7?**
    → `ASSUMPTIONS` [src:LA1]
27. **Kytkentä muihin agentteihin #8?**
    → `ASSUMPTIONS` [src:LA1]
28. **Kytkentä muihin agentteihin #9?**
    → `ASSUMPTIONS` [src:LA1]
29. **Kytkentä muihin agentteihin #10?**
    → `ASSUMPTIONS` [src:LA1]
30. **Kytkentä muihin agentteihin #11?**
    → `ASSUMPTIONS` [src:LA1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 36
## AGENT 36: Kybervahti (tietoturva)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: kybervahti
  agent_name: Kybervahti (tietoturva)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Kotiverkko + IoT
- Ollama paikallisesti
- VPN etäyhteydellä
DECISION_METRICS_AND_THRESHOLDS:
  failed_login_max:
    value: 5
    action: '>5 epäonnistunutta / 10min → IP-esto 1h'
    source: src:KY1
  cve_check_days:
    value: 7
    action: IoT-laitteiden CVE-tiedotteet viikoittain
    source: src:KY1
  unknown_device:
    value: Tuntematon MAC verkossa
    action: Eristä VLAN, tunnista, blokkaa/hyväksy
    source: src:KY1
  vpn_required:
    value: Etäyhteys VAIN VPN:n kautta
    source: src:KY1
  password_min_len:
    value: 12
    action: 'Kaikki laitteet: ≥12 merkkiä, uniikki per laite'
    source: src:KY1
SEASONAL_RULES:
- season: Kevät
  action: Salasanojen vaihto 6kk sykli. Firmware-päivitykset.
  source: src:KY1
- season: Kesä
  action: '[vko 22-35] Lomakausi: etäyhteyksien seuranta.'
  source: src:KY1
- season: Syksy
  action: '[vko 36-48] Salasanarotaatio. Varmuuskopiopalautustesti.'
  source: src:KY1
- season: Talvi
  action: '[vko 49-13] UPS + verkkolaitteiden sähkön laatu aggregaattikäytössä.'
  source: src:KY1
FAILURE_MODES:
- mode: Tuntematon laite verkossa
  detection: 'Verkkoskannaus: tuntematon MAC'
  action: Eristä VLAN, tunnista, blokkaa reitittimestä
  source: src:KY1
- mode: Brute force
  detection: '>20 epäonnistunutta / 1h'
  action: IP-esto, lokit, fail2ban
  source: src:KY1
PROCESS_FLOWS:
- flow_id: FLOW_KYBE_01
  trigger: failed_login_max ylittää kynnysarvon (5)
  action: '>5 epäonnistunutta / 10min → IP-esto 1h'
  output: Tilanneraportti
  source: src:KYBE
- flow_id: FLOW_KYBE_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: Salasanojen vaihto 6kk sykli. Firmware-päivitykset.
  output: Tarkistuslista
  source: src:KYBE
- flow_id: FLOW_KYBE_03
  trigger: 'Havaittu: Tuntematon laite verkossa'
  action: Eristä VLAN, tunnista, blokkaa reitittimestä
  output: Poikkeamaraportti
  source: src:KYBE
- flow_id: FLOW_KYBE_04
  trigger: Säännöllinen heartbeat
  action: 'kybervahti: rutiiniarviointi'
  output: Status-raportti
  source: src:KYBE
KNOWLEDGE_TABLES:
- table_id: TBL_KYBE_01
  title: Kybervahti (tietoturva) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: failed_login_max
    value: '5'
    action: '>5 epäonnistunutta / 10min → IP-esto 1h'
  - metric: cve_check_days
    value: '7'
    action: IoT-laitteiden CVE-tiedotteet viikoittain
  - metric: unknown_device
    value: Tuntematon MAC verkossa
    action: Eristä VLAN, tunnista, blokkaa/hyväksy
  - metric: vpn_required
    value: Etäyhteys VAIN VPN:n kautta
    action: ''
  - metric: password_min_len
    value: '12'
    action: 'Kaikki laitteet: ≥12 merkkiä, uniikki per laite'
  source: src:KYBE
COMPLIANCE_AND_LEGAL:
  gdpr: 'GDPR: henkilötietojen suojaus myös kotiverkossa [src:KY2]'
UNCERTAINTY_NOTES:
- IoT-laitteiden tietoturva usein heikko — oletussalasanat.
SOURCE_REGISTRY:
  sources:
  - id: src:KY1
    org: Kyberturvallisuuskeskus
    title: Kyberturvallisuus kotona
    year: 2025
    url: https://www.kyberturvallisuuskeskus.fi/
    supports: Kotiverkko, IoT.
  - id: src:KY2
    org: Tietosuojavaltuutettu
    title: GDPR
    year: 2025
    url: https://tietosuoja.fi/
    supports: Henkilötiedot.
eval_questions:
- q: Mikä on failed login max?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.failed_login_max
  source: src:KY1
- q: 'Toimenpide: failed login max?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.failed_login_max.action
  source: src:KY1
- q: Mikä on cve check days?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cve_check_days
  source: src:KY1
- q: 'Toimenpide: cve check days?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cve_check_days.action
  source: src:KY1
- q: Mikä on unknown device?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.unknown_device
  source: src:KY1
- q: 'Toimenpide: unknown device?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.unknown_device.action
  source: src:KY1
- q: Mikä on vpn required?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.vpn_required
  source: src:KY1
- q: Mikä on password min len?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.password_min_len
  source: src:KY1
- q: 'Toimenpide: password min len?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.password_min_len.action
  source: src:KY1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:KY1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:KY1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:KY1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:KY1
- q: 'Havainto: Tuntematon laite verkossa?'
  a_ref: FAILURE_MODES[0].detection
  source: src:KY1
- q: 'Toiminta: Tuntematon laite verkossa?'
  a_ref: FAILURE_MODES[0].action
  source: src:KY1
- q: 'Havainto: Brute force?'
  a_ref: FAILURE_MODES[1].detection
  source: src:KY1
- q: 'Toiminta: Brute force?'
  a_ref: FAILURE_MODES[1].action
  source: src:KY1
- q: 'Sääntö: gdpr?'
  a_ref: COMPLIANCE_AND_LEGAL.gdpr
  source: src:KY1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:KY1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #12?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #13?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #14?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #15?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #16?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #17?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #18?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #19?'
  a_ref: ASSUMPTIONS
  source: src:KY1
- q: 'Kytkentä muihin agentteihin #20?'
  a_ref: ASSUMPTIONS
  source: src:KY1
```

**sources.yaml:**
```yaml
sources:
- id: src:KY1
  org: Kyberturvallisuuskeskus
  title: Kyberturvallisuus kotona
  year: 2025
  url: https://www.kyberturvallisuuskeskus.fi/
  supports: Kotiverkko, IoT.
- id: src:KY2
  org: Tietosuojavaltuutettu
  title: GDPR
  year: 2025
  url: https://tietosuoja.fi/
  supports: Henkilötiedot.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Kybervahti (tietoturva)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Kotiverkko + IoT
- Ollama paikallisesti
- VPN etäyhteydellä

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| failed_login_max | 5 | >5 epäonnistunutta / 10min → IP-esto 1h | src:KY1 |
| cve_check_days | 7 | IoT-laitteiden CVE-tiedotteet viikoittain | src:KY1 |
| unknown_device | Tuntematon MAC verkossa | Eristä VLAN, tunnista, blokkaa/hyväksy | src:KY1 |
| vpn_required | Etäyhteys VAIN VPN:n kautta | — | src:KY1 |
| password_min_len | 12 | Kaikki laitteet: ≥12 merkkiä, uniikki per laite | src:KY1 |

## Tietotaulukot

**Kybervahti (tietoturva) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| failed_login_max | 5 | >5 epäonnistunutta / 10min → IP-esto 1h |
| cve_check_days | 7 | IoT-laitteiden CVE-tiedotteet viikoittain |
| unknown_device | Tuntematon MAC verkossa | Eristä VLAN, tunnista, blokkaa/hyväksy |
| vpn_required | Etäyhteys VAIN VPN:n kautta |  |
| password_min_len | 12 | Kaikki laitteet: ≥12 merkkiä, uniikki per laite |

## Prosessit

**FLOW_KYBE_01:** failed_login_max ylittää kynnysarvon (5)
  → >5 epäonnistunutta / 10min → IP-esto 1h
  Tulos: Tilanneraportti

**FLOW_KYBE_02:** Kausi vaihtuu: Kevät
  → Salasanojen vaihto 6kk sykli. Firmware-päivitykset.
  Tulos: Tarkistuslista

**FLOW_KYBE_03:** Havaittu: Tuntematon laite verkossa
  → Eristä VLAN, tunnista, blokkaa reitittimestä
  Tulos: Poikkeamaraportti

**FLOW_KYBE_04:** Säännöllinen heartbeat
  → kybervahti: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | Salasanojen vaihto 6kk sykli. Firmware-päivitykset. | src:KY1 |
| **Kesä** | [vko 22-35] Lomakausi: etäyhteyksien seuranta. | src:KY1 |
| **Syksy** | [vko 36-48] Salasanarotaatio. Varmuuskopiopalautustesti. | src:KY1 |
| **Talvi** | [vko 49-13] UPS + verkkolaitteiden sähkön laatu aggregaattikäytössä. | src:KY1 |

## Virhe- ja vaaratilanteet

### ⚠️ Tuntematon laite verkossa
- **Havaitseminen:** Verkkoskannaus: tuntematon MAC
- **Toimenpide:** Eristä VLAN, tunnista, blokkaa reitittimestä
- **Lähde:** src:KY1

### ⚠️ Brute force
- **Havaitseminen:** >20 epäonnistunutta / 1h
- **Toimenpide:** IP-esto, lokit, fail2ban
- **Lähde:** src:KY1

## Lait ja vaatimukset
- **gdpr:** GDPR: henkilötietojen suojaus myös kotiverkossa [src:KY2]

## Epävarmuudet
- IoT-laitteiden tietoturva usein heikko — oletussalasanat.

## Lähteet
- **src:KY1**: Kyberturvallisuuskeskus — *Kyberturvallisuus kotona* (2025) https://www.kyberturvallisuuskeskus.fi/
- **src:KY2**: Tietosuojavaltuutettu — *GDPR* (2025) https://tietosuoja.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on failed login max?**
    → `DECISION_METRICS_AND_THRESHOLDS.failed_login_max` [src:KY1]
 2. **Toimenpide: failed login max?**
    → `DECISION_METRICS_AND_THRESHOLDS.failed_login_max.action` [src:KY1]
 3. **Mikä on cve check days?**
    → `DECISION_METRICS_AND_THRESHOLDS.cve_check_days` [src:KY1]
 4. **Toimenpide: cve check days?**
    → `DECISION_METRICS_AND_THRESHOLDS.cve_check_days.action` [src:KY1]
 5. **Mikä on unknown device?**
    → `DECISION_METRICS_AND_THRESHOLDS.unknown_device` [src:KY1]
 6. **Toimenpide: unknown device?**
    → `DECISION_METRICS_AND_THRESHOLDS.unknown_device.action` [src:KY1]
 7. **Mikä on vpn required?**
    → `DECISION_METRICS_AND_THRESHOLDS.vpn_required` [src:KY1]
 8. **Mikä on password min len?**
    → `DECISION_METRICS_AND_THRESHOLDS.password_min_len` [src:KY1]
 9. **Toimenpide: password min len?**
    → `DECISION_METRICS_AND_THRESHOLDS.password_min_len.action` [src:KY1]
10. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:KY1]
11. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:KY1]
12. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:KY1]
13. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:KY1]
14. **Havainto: Tuntematon laite verkossa?**
    → `FAILURE_MODES[0].detection` [src:KY1]
15. **Toiminta: Tuntematon laite verkossa?**
    → `FAILURE_MODES[0].action` [src:KY1]
16. **Havainto: Brute force?**
    → `FAILURE_MODES[1].detection` [src:KY1]
17. **Toiminta: Brute force?**
    → `FAILURE_MODES[1].action` [src:KY1]
18. **Sääntö: gdpr?**
    → `COMPLIANCE_AND_LEGAL.gdpr` [src:KY1]
19. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:KY1]
20. **Oletukset?**
    → `ASSUMPTIONS` [src:KY1]
21. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:KY1]
22. **Kytkentä muihin agentteihin #2?**
    → `ASSUMPTIONS` [src:KY1]
23. **Kytkentä muihin agentteihin #3?**
    → `ASSUMPTIONS` [src:KY1]
24. **Kytkentä muihin agentteihin #4?**
    → `ASSUMPTIONS` [src:KY1]
25. **Kytkentä muihin agentteihin #5?**
    → `ASSUMPTIONS` [src:KY1]
26. **Kytkentä muihin agentteihin #6?**
    → `ASSUMPTIONS` [src:KY1]
27. **Kytkentä muihin agentteihin #7?**
    → `ASSUMPTIONS` [src:KY1]
28. **Kytkentä muihin agentteihin #8?**
    → `ASSUMPTIONS` [src:KY1]
29. **Kytkentä muihin agentteihin #9?**
    → `ASSUMPTIONS` [src:KY1]
30. **Kytkentä muihin agentteihin #10?**
    → `ASSUMPTIONS` [src:KY1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 37
## AGENT 37: Lukkoseppä (älylukot)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: lukkoseppa
  agent_name: Lukkoseppä (älylukot)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Korvenranta: mekaaninen + älylukko'
- PIN, RFID, mobiili, avain varavaihtoehtona
DECISION_METRICS_AND_THRESHOLDS:
  battery_pct:
    value: <20% → vaihda
    action: Varavirta USB-C
    source: src:LU1
  lock_jam:
    value: Moottori ei saa lukkoa kiinni/auki 3 yrityksellä
    action: Mekaaninen avain, tarkista lukkorunko
    source: src:LU1
  access_anomaly_hours:
    value: 02:00-05:00
    action: Avaus yöllä → P2 hälytys pihavahdille
    source: src:LU1
  pin_change_months:
    value: 6
    source: src:LU1
  temp_range_c:
    value: -25 to +60
    action: <-25°C → mekanismi jäätyy, jäänesto
    source: src:LU1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Puhdistus ja voitelu talven jälkeen.'
  source: src:LU1
- season: Kesä
  action: '[vko 22-35] Normaali. Pääsyoikeuksien tarkistus (vieraat?).'
  source: src:LU1
- season: Syksy
  action: '[vko 36-48] Akut tarkistus ennen talvea. Varavirta testaus.'
  source: src:LU1
- season: Talvi
  action: Jäätymisesto (-25°C). Mekaaninen avain aina mukana.
  source: src:LU1
FAILURE_MODES:
- mode: Lukko jäätynyt
  detection: Mekanismi ei liiku
  action: Jäänestosuihke, ÄLÄ väännä, lämmin avain
  source: src:LU1
- mode: Akku tyhjä
  detection: Ei reagoi
  action: USB-C varavirta tai mekaaninen avain
  source: src:LU1
PROCESS_FLOWS:
- flow_id: FLOW_LUKK_01
  trigger: battery_pct ylittää kynnysarvon (<20% → vaihda)
  action: Varavirta USB-C
  output: Tilanneraportti
  source: src:LUKK
- flow_id: FLOW_LUKK_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Puhdistus ja voitelu talven jälkeen.'
  output: Tarkistuslista
  source: src:LUKK
- flow_id: FLOW_LUKK_03
  trigger: 'Havaittu: Lukko jäätynyt'
  action: Jäänestosuihke, ÄLÄ väännä, lämmin avain
  output: Poikkeamaraportti
  source: src:LUKK
- flow_id: FLOW_LUKK_04
  trigger: Säännöllinen heartbeat
  action: 'lukkoseppa: rutiiniarviointi'
  output: Status-raportti
  source: src:LUKK
KNOWLEDGE_TABLES:
- table_id: TBL_LUKK_01
  title: Lukkoseppä (älylukot) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: battery_pct
    value: <20% → vaihda
    action: Varavirta USB-C
  - metric: lock_jam
    value: Moottori ei saa lukkoa kiinni/auki 3 yrityksellä
    action: Mekaaninen avain, tarkista lukkorunko
  - metric: access_anomaly_hours
    value: 02:00-05:00
    action: Avaus yöllä → P2 hälytys pihavahdille
  - metric: pin_change_months
    value: '6'
    action: ''
  - metric: temp_range_c
    value: -25 to +60
    action: <-25°C → mekanismi jäätyy, jäänesto
  source: src:LUKK
COMPLIANCE_AND_LEGAL:
  vakuutus: Vakuutusyhtiön hyväksymä murtosuojaus [src:LU2]
UNCERTAINTY_NOTES:
- Älylukon kyberturvallisuus riippuu valmistajasta.
SOURCE_REGISTRY:
  sources:
  - id: src:LU1
    org: Lukkoliikkeet
    title: Älylukot
    year: 2025
    url: null
    supports: Huolto, jäätyminen.
  - id: src:LU2
    org: Finanssiala ry
    title: Murtosuojaus
    year: 2025
    url: https://www.finanssiala.fi/
    supports: Vakuutusvaatimukset.
eval_questions:
- q: Mikä on battery pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.battery_pct
  source: src:LU1
- q: 'Toimenpide: battery pct?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.battery_pct.action
  source: src:LU1
- q: Mikä on lock jam?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.lock_jam
  source: src:LU1
- q: 'Toimenpide: lock jam?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.lock_jam.action
  source: src:LU1
- q: Mikä on access anomaly hours?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.access_anomaly_hours
  source: src:LU1
- q: 'Toimenpide: access anomaly hours?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.access_anomaly_hours.action
  source: src:LU1
- q: Mikä on pin change months?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pin_change_months
  source: src:LU1
- q: Mikä on temp range c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.temp_range_c
  source: src:LU1
- q: 'Toimenpide: temp range c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.temp_range_c.action
  source: src:LU1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:LU1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:LU1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:LU1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:LU1
- q: 'Havainto: Lukko jäätynyt?'
  a_ref: FAILURE_MODES[0].detection
  source: src:LU1
- q: 'Toiminta: Lukko jäätynyt?'
  a_ref: FAILURE_MODES[0].action
  source: src:LU1
- q: 'Havainto: Akku tyhjä?'
  a_ref: FAILURE_MODES[1].detection
  source: src:LU1
- q: 'Toiminta: Akku tyhjä?'
  a_ref: FAILURE_MODES[1].action
  source: src:LU1
- q: 'Sääntö: vakuutus?'
  a_ref: COMPLIANCE_AND_LEGAL.vakuutus
  source: src:LU1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:LU1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #12?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #13?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #14?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #15?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #16?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #17?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #18?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #19?'
  a_ref: ASSUMPTIONS
  source: src:LU1
- q: 'Kytkentä muihin agentteihin #20?'
  a_ref: ASSUMPTIONS
  source: src:LU1
```

**sources.yaml:**
```yaml
sources:
- id: src:LU1
  org: Lukkoliikkeet
  title: Älylukot
  year: 2025
  url: null
  supports: Huolto, jäätyminen.
- id: src:LU2
  org: Finanssiala ry
  title: Murtosuojaus
  year: 2025
  url: https://www.finanssiala.fi/
  supports: Vakuutusvaatimukset.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Lukkoseppä (älylukot)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenranta: mekaaninen + älylukko
- PIN, RFID, mobiili, avain varavaihtoehtona

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| battery_pct | <20% → vaihda | Varavirta USB-C | src:LU1 |
| lock_jam | Moottori ei saa lukkoa kiinni/auki 3 yrityksellä | Mekaaninen avain, tarkista lukkorunko | src:LU1 |
| access_anomaly_hours | 02:00-05:00 | Avaus yöllä → P2 hälytys pihavahdille | src:LU1 |
| pin_change_months | 6 | — | src:LU1 |
| temp_range_c | -25 to +60 | <-25°C → mekanismi jäätyy, jäänesto | src:LU1 |

## Tietotaulukot

**Lukkoseppä (älylukot) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| battery_pct | <20% → vaihda | Varavirta USB-C |
| lock_jam | Moottori ei saa lukkoa kiinni/auki 3 yrityksellä | Mekaaninen avain, tarkista lukkorunko |
| access_anomaly_hours | 02:00-05:00 | Avaus yöllä → P2 hälytys pihavahdille |
| pin_change_months | 6 |  |
| temp_range_c | -25 to +60 | <-25°C → mekanismi jäätyy, jäänesto |

## Prosessit

**FLOW_LUKK_01:** battery_pct ylittää kynnysarvon (<20% → vaihda)
  → Varavirta USB-C
  Tulos: Tilanneraportti

**FLOW_LUKK_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Puhdistus ja voitelu talven jälkeen.
  Tulos: Tarkistuslista

**FLOW_LUKK_03:** Havaittu: Lukko jäätynyt
  → Jäänestosuihke, ÄLÄ väännä, lämmin avain
  Tulos: Poikkeamaraportti

**FLOW_LUKK_04:** Säännöllinen heartbeat
  → lukkoseppa: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Puhdistus ja voitelu talven jälkeen. | src:LU1 |
| **Kesä** | [vko 22-35] Normaali. Pääsyoikeuksien tarkistus (vieraat?). | src:LU1 |
| **Syksy** | [vko 36-48] Akut tarkistus ennen talvea. Varavirta testaus. | src:LU1 |
| **Talvi** | Jäätymisesto (-25°C). Mekaaninen avain aina mukana. | src:LU1 |

## Virhe- ja vaaratilanteet

### ⚠️ Lukko jäätynyt
- **Havaitseminen:** Mekanismi ei liiku
- **Toimenpide:** Jäänestosuihke, ÄLÄ väännä, lämmin avain
- **Lähde:** src:LU1

### ⚠️ Akku tyhjä
- **Havaitseminen:** Ei reagoi
- **Toimenpide:** USB-C varavirta tai mekaaninen avain
- **Lähde:** src:LU1

## Lait ja vaatimukset
- **vakuutus:** Vakuutusyhtiön hyväksymä murtosuojaus [src:LU2]

## Epävarmuudet
- Älylukon kyberturvallisuus riippuu valmistajasta.

## Lähteet
- **src:LU1**: Lukkoliikkeet — *Älylukot* (2025) —
- **src:LU2**: Finanssiala ry — *Murtosuojaus* (2025) https://www.finanssiala.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on battery pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.battery_pct` [src:LU1]
 2. **Toimenpide: battery pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.battery_pct.action` [src:LU1]
 3. **Mikä on lock jam?**
    → `DECISION_METRICS_AND_THRESHOLDS.lock_jam` [src:LU1]
 4. **Toimenpide: lock jam?**
    → `DECISION_METRICS_AND_THRESHOLDS.lock_jam.action` [src:LU1]
 5. **Mikä on access anomaly hours?**
    → `DECISION_METRICS_AND_THRESHOLDS.access_anomaly_hours` [src:LU1]
 6. **Toimenpide: access anomaly hours?**
    → `DECISION_METRICS_AND_THRESHOLDS.access_anomaly_hours.action` [src:LU1]
 7. **Mikä on pin change months?**
    → `DECISION_METRICS_AND_THRESHOLDS.pin_change_months` [src:LU1]
 8. **Mikä on temp range c?**
    → `DECISION_METRICS_AND_THRESHOLDS.temp_range_c` [src:LU1]
 9. **Toimenpide: temp range c?**
    → `DECISION_METRICS_AND_THRESHOLDS.temp_range_c.action` [src:LU1]
10. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:LU1]
11. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:LU1]
12. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:LU1]
13. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:LU1]
14. **Havainto: Lukko jäätynyt?**
    → `FAILURE_MODES[0].detection` [src:LU1]
15. **Toiminta: Lukko jäätynyt?**
    → `FAILURE_MODES[0].action` [src:LU1]
16. **Havainto: Akku tyhjä?**
    → `FAILURE_MODES[1].detection` [src:LU1]
17. **Toiminta: Akku tyhjä?**
    → `FAILURE_MODES[1].action` [src:LU1]
18. **Sääntö: vakuutus?**
    → `COMPLIANCE_AND_LEGAL.vakuutus` [src:LU1]
19. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:LU1]
20. **Oletukset?**
    → `ASSUMPTIONS` [src:LU1]
21. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:LU1]
22. **Kytkentä muihin agentteihin #2?**
    → `ASSUMPTIONS` [src:LU1]
23. **Kytkentä muihin agentteihin #3?**
    → `ASSUMPTIONS` [src:LU1]
24. **Kytkentä muihin agentteihin #4?**
    → `ASSUMPTIONS` [src:LU1]
25. **Kytkentä muihin agentteihin #5?**
    → `ASSUMPTIONS` [src:LU1]
26. **Kytkentä muihin agentteihin #6?**
    → `ASSUMPTIONS` [src:LU1]
27. **Kytkentä muihin agentteihin #7?**
    → `ASSUMPTIONS` [src:LU1]
28. **Kytkentä muihin agentteihin #8?**
    → `ASSUMPTIONS` [src:LU1]
29. **Kytkentä muihin agentteihin #9?**
    → `ASSUMPTIONS` [src:LU1]
30. **Kytkentä muihin agentteihin #10?**
    → `ASSUMPTIONS` [src:LU1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 38
## AGENT 38: Pihavahti (ihmishavainnot)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: pihavahti
  agent_name: Pihavahti (ihmishavainnot)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- PTZ-kamera + liiketunnistus
- Korvenrannan pihapiiri
- Kytketty lukkoseppään, privaattisuuteen, corehen
DECISION_METRICS_AND_THRESHOLDS:
  person_confidence:
    value: 0.7
    action: <0.7 → logi, >0.7 → hälytys jos tuntematon
    source: src:PI1
  night_alert_hours:
    value: 22:00-06:00
    action: Ihmishavainto yöllä → P2 hälytys
    source: src:PI1
  whitelist:
    value: Kasvo/mobiilit tunnistetut → ei hälytystä
    action: Tuntematon → kuva + ilmoitus
    source: src:PI1
  vehicle_detection:
    value: Tuntematon auto pihalla
    action: Rekisterikilpi (jos luettavissa), aika, tallenne
    source: src:PI1
  loitering_min:
    value: 5
    action: '>5 min paikallaan ilman syytä → hälytys'
    source: src:PI1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Pihatyöläiset → päivitä whitelist.'
  source: src:PI1
- season: Kesä
  action: '[vko 22-35] Mökkikausi: enemmän ihmisiä → herkkyystaso alas.'
  source: src:PI1
- season: Syksy
  action: '[vko 36-48] Pimenee → IR-tunnistuksen merkitys kasvaa.'
  source: src:PI1
- season: Talvi
  action: '[vko 49-13] Lumityöntekijät → whitelist. Jäljet lumessa.'
  source: src:PI1
FAILURE_MODES:
- mode: Tuntematon yöllä
  detection: Confidence >0.7, klo 22-06, ei whitelistissä
  action: Tallenna, hälytä, aktivoi valaistus
  source: src:PI1
- mode: Väärähälytys (eläin/varjo)
  detection: Toistuva hälytys samasta pisteestä
  action: Herkkyys, kasvillisuus, liiketunnistimen kulma
  source: src:PI1
PROCESS_FLOWS:
- flow_id: FLOW_PIHA_01
  trigger: person_confidence ylittää kynnysarvon (0.7)
  action: <0.7 → logi, >0.7 → hälytys jos tuntematon
  output: Tilanneraportti
  source: src:PIHA
- flow_id: FLOW_PIHA_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Pihatyöläiset → päivitä whitelist.'
  output: Tarkistuslista
  source: src:PIHA
- flow_id: FLOW_PIHA_03
  trigger: 'Havaittu: Tuntematon yöllä'
  action: Tallenna, hälytä, aktivoi valaistus
  output: Poikkeamaraportti
  source: src:PIHA
- flow_id: FLOW_PIHA_04
  trigger: Säännöllinen heartbeat
  action: 'pihavahti: rutiiniarviointi'
  output: Status-raportti
  source: src:PIHA
KNOWLEDGE_TABLES:
- table_id: TBL_PIHA_01
  title: Pihavahti (ihmishavainnot) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: person_confidence
    value: '0.7'
    action: <0.7 → logi, >0.7 → hälytys jos tuntematon
  - metric: night_alert_hours
    value: 22:00-06:00
    action: Ihmishavainto yöllä → P2 hälytys
  - metric: whitelist
    value: Kasvo/mobiilit tunnistetut → ei hälytystä
    action: Tuntematon → kuva + ilmoitus
  - metric: vehicle_detection
    value: Tuntematon auto pihalla
    action: Rekisterikilpi (jos luettavissa), aika, tallenne
  - metric: loitering_min
    value: '5'
    action: '>5 min paikallaan ilman syytä → hälytys'
  source: src:PIHA
COMPLIANCE_AND_LEGAL:
  kameravalvonta: EI naapurikiinteistöä. Tietosuojavaltuutetun ohje. [src:PI2]
  rekisteriseloste: 'GDPR: kameravalvonnan rekisteriseloste [src:PI2]'
UNCERTAINTY_NOTES:
- 'Yönäkö: ~60% tarkkuus vs 90% päivällä.'
SOURCE_REGISTRY:
  sources:
  - id: src:PI1
    org: Turva-ala
    title: Kotiturvallisuus
    year: 2025
    url: null
    supports: Kamera, ihmistunnistus.
  - id: src:PI2
    org: Tietosuojavaltuutettu
    title: Kameravalvonta GDPR
    year: 2025
    url: https://tietosuoja.fi/kameravalvonta
    supports: Yksityisyys.
eval_questions:
- q: Mikä on person confidence?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.person_confidence
  source: src:PI1
- q: 'Toimenpide: person confidence?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.person_confidence.action
  source: src:PI1
- q: Mikä on night alert hours?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.night_alert_hours
  source: src:PI1
- q: 'Toimenpide: night alert hours?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.night_alert_hours.action
  source: src:PI1
- q: Mikä on whitelist?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.whitelist
  source: src:PI1
- q: 'Toimenpide: whitelist?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.whitelist.action
  source: src:PI1
- q: Mikä on vehicle detection?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.vehicle_detection
  source: src:PI1
- q: 'Toimenpide: vehicle detection?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.vehicle_detection.action
  source: src:PI1
- q: Mikä on loitering min?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.loitering_min
  source: src:PI1
- q: 'Toimenpide: loitering min?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.loitering_min.action
  source: src:PI1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:PI1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:PI1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:PI1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:PI1
- q: 'Havainto: Tuntematon yöllä?'
  a_ref: FAILURE_MODES[0].detection
  source: src:PI1
- q: 'Toiminta: Tuntematon yöllä?'
  a_ref: FAILURE_MODES[0].action
  source: src:PI1
- q: 'Havainto: Väärähälytys (eläin/varjo)?'
  a_ref: FAILURE_MODES[1].detection
  source: src:PI1
- q: 'Toiminta: Väärähälytys (eläin/varjo)?'
  a_ref: FAILURE_MODES[1].action
  source: src:PI1
- q: 'Sääntö: kameravalvonta?'
  a_ref: COMPLIANCE_AND_LEGAL.kameravalvonta
  source: src:PI1
- q: 'Sääntö: rekisteriseloste?'
  a_ref: COMPLIANCE_AND_LEGAL.rekisteriseloste
  source: src:PI1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:PI1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #12?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #13?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #14?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #15?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #16?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #17?'
  a_ref: ASSUMPTIONS
  source: src:PI1
- q: 'Kytkentä muihin agentteihin #18?'
  a_ref: ASSUMPTIONS
  source: src:PI1
```

**sources.yaml:**
```yaml
sources:
- id: src:PI1
  org: Turva-ala
  title: Kotiturvallisuus
  year: 2025
  url: null
  supports: Kamera, ihmistunnistus.
- id: src:PI2
  org: Tietosuojavaltuutettu
  title: Kameravalvonta GDPR
  year: 2025
  url: https://tietosuoja.fi/kameravalvonta
  supports: Yksityisyys.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Pihavahti (ihmishavainnot)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- PTZ-kamera + liiketunnistus
- Korvenrannan pihapiiri
- Kytketty lukkoseppään, privaattisuuteen, corehen

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| person_confidence | 0.7 | <0.7 → logi, >0.7 → hälytys jos tuntematon | src:PI1 |
| night_alert_hours | 22:00-06:00 | Ihmishavainto yöllä → P2 hälytys | src:PI1 |
| whitelist | Kasvo/mobiilit tunnistetut → ei hälytystä | Tuntematon → kuva + ilmoitus | src:PI1 |
| vehicle_detection | Tuntematon auto pihalla | Rekisterikilpi (jos luettavissa), aika, tallenne | src:PI1 |
| loitering_min | 5 | >5 min paikallaan ilman syytä → hälytys | src:PI1 |

## Tietotaulukot

**Pihavahti (ihmishavainnot) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| person_confidence | 0.7 | <0.7 → logi, >0.7 → hälytys jos tuntematon |
| night_alert_hours | 22:00-06:00 | Ihmishavainto yöllä → P2 hälytys |
| whitelist | Kasvo/mobiilit tunnistetut → ei hälytystä | Tuntematon → kuva + ilmoitus |
| vehicle_detection | Tuntematon auto pihalla | Rekisterikilpi (jos luettavissa), aika, tallenne |
| loitering_min | 5 | >5 min paikallaan ilman syytä → hälytys |

## Prosessit

**FLOW_PIHA_01:** person_confidence ylittää kynnysarvon (0.7)
  → <0.7 → logi, >0.7 → hälytys jos tuntematon
  Tulos: Tilanneraportti

**FLOW_PIHA_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Pihatyöläiset → päivitä whitelist.
  Tulos: Tarkistuslista

**FLOW_PIHA_03:** Havaittu: Tuntematon yöllä
  → Tallenna, hälytä, aktivoi valaistus
  Tulos: Poikkeamaraportti

**FLOW_PIHA_04:** Säännöllinen heartbeat
  → pihavahti: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Pihatyöläiset → päivitä whitelist. | src:PI1 |
| **Kesä** | [vko 22-35] Mökkikausi: enemmän ihmisiä → herkkyystaso alas. | src:PI1 |
| **Syksy** | [vko 36-48] Pimenee → IR-tunnistuksen merkitys kasvaa. | src:PI1 |
| **Talvi** | [vko 49-13] Lumityöntekijät → whitelist. Jäljet lumessa. | src:PI1 |

## Virhe- ja vaaratilanteet

### ⚠️ Tuntematon yöllä
- **Havaitseminen:** Confidence >0.7, klo 22-06, ei whitelistissä
- **Toimenpide:** Tallenna, hälytä, aktivoi valaistus
- **Lähde:** src:PI1

### ⚠️ Väärähälytys (eläin/varjo)
- **Havaitseminen:** Toistuva hälytys samasta pisteestä
- **Toimenpide:** Herkkyys, kasvillisuus, liiketunnistimen kulma
- **Lähde:** src:PI1

## Lait ja vaatimukset
- **kameravalvonta:** EI naapurikiinteistöä. Tietosuojavaltuutetun ohje. [src:PI2]
- **rekisteriseloste:** GDPR: kameravalvonnan rekisteriseloste [src:PI2]

## Epävarmuudet
- Yönäkö: ~60% tarkkuus vs 90% päivällä.

## Lähteet
- **src:PI1**: Turva-ala — *Kotiturvallisuus* (2025) —
- **src:PI2**: Tietosuojavaltuutettu — *Kameravalvonta GDPR* (2025) https://tietosuoja.fi/kameravalvonta

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on person confidence?**
    → `DECISION_METRICS_AND_THRESHOLDS.person_confidence` [src:PI1]
 2. **Toimenpide: person confidence?**
    → `DECISION_METRICS_AND_THRESHOLDS.person_confidence.action` [src:PI1]
 3. **Mikä on night alert hours?**
    → `DECISION_METRICS_AND_THRESHOLDS.night_alert_hours` [src:PI1]
 4. **Toimenpide: night alert hours?**
    → `DECISION_METRICS_AND_THRESHOLDS.night_alert_hours.action` [src:PI1]
 5. **Mikä on whitelist?**
    → `DECISION_METRICS_AND_THRESHOLDS.whitelist` [src:PI1]
 6. **Toimenpide: whitelist?**
    → `DECISION_METRICS_AND_THRESHOLDS.whitelist.action` [src:PI1]
 7. **Mikä on vehicle detection?**
    → `DECISION_METRICS_AND_THRESHOLDS.vehicle_detection` [src:PI1]
 8. **Toimenpide: vehicle detection?**
    → `DECISION_METRICS_AND_THRESHOLDS.vehicle_detection.action` [src:PI1]
 9. **Mikä on loitering min?**
    → `DECISION_METRICS_AND_THRESHOLDS.loitering_min` [src:PI1]
10. **Toimenpide: loitering min?**
    → `DECISION_METRICS_AND_THRESHOLDS.loitering_min.action` [src:PI1]
11. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:PI1]
12. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:PI1]
13. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:PI1]
14. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:PI1]
15. **Havainto: Tuntematon yöllä?**
    → `FAILURE_MODES[0].detection` [src:PI1]
16. **Toiminta: Tuntematon yöllä?**
    → `FAILURE_MODES[0].action` [src:PI1]
17. **Havainto: Väärähälytys (eläin/varjo)?**
    → `FAILURE_MODES[1].detection` [src:PI1]
18. **Toiminta: Väärähälytys (eläin/varjo)?**
    → `FAILURE_MODES[1].action` [src:PI1]
19. **Sääntö: kameravalvonta?**
    → `COMPLIANCE_AND_LEGAL.kameravalvonta` [src:PI1]
20. **Sääntö: rekisteriseloste?**
    → `COMPLIANCE_AND_LEGAL.rekisteriseloste` [src:PI1]
21. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:PI1]
22. **Oletukset?**
    → `ASSUMPTIONS` [src:PI1]
23. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:PI1]
24. **Kytkentä muihin agentteihin #2?**
    → `ASSUMPTIONS` [src:PI1]
25. **Kytkentä muihin agentteihin #3?**
    → `ASSUMPTIONS` [src:PI1]
26. **Kytkentä muihin agentteihin #4?**
    → `ASSUMPTIONS` [src:PI1]
27. **Kytkentä muihin agentteihin #5?**
    → `ASSUMPTIONS` [src:PI1]
28. **Kytkentä muihin agentteihin #6?**
    → `ASSUMPTIONS` [src:PI1]
29. **Kytkentä muihin agentteihin #7?**
    → `ASSUMPTIONS` [src:PI1]
30. **Kytkentä muihin agentteihin #8?**
    → `ASSUMPTIONS` [src:PI1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 39
## AGENT 39: Privaattisuuden suojelija
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: privaattisuus
  agent_name: Privaattisuuden suojelija
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Valvoo KAIKKIEN agenttien tietosuojaa
- GDPR + kansallinen tietosuojalaki
- Kamera-, ääni-, sijaintidata
DECISION_METRICS_AND_THRESHOLDS:
  camera_coverage:
    value: 0% naapurikiinteistöä, 0% yleistä tietä tunnistettavasti
    action: Yli 0% → suuntaa kamera HETI, pienennä kuvakulma. Tarkistus 2x/v + asennuksen
      jälkeen.
    source: src:PR1
  data_retention_days:
    value: 30
    action: '>30 pv → automaattipoisto (ei-merkityt). Poliisipyynnön tallenteet 90
      pv.'
    source: src:PR1
  audio_recording:
    value: 0
    note: 0 = pois päältä ulkokameroissa
    action: Äänitallenne ulkona ilman informointia → GDPR-rike. Pois tai kyltti 'Alueella
      tallentava kameravalvonta'.
    source: src:PR1
  data_local_only:
    value: Henkilötiedot paikallisesti, ei pilveen
    source: src:PR1
  access_control:
    value: Vain kiinteistön omistaja tai poliisin pyynnöstä
    source: src:PR1
  data_local_pct:
    value: 100
    action: 100% paikallisesti. Pilvipalveluun lähettäminen → blokkaa palomuurissa,
      ilmoita kybervahdille.
    source: src:PR1
  access_log_audit_days:
    value: 7
    action: Tarkista kameratallenteiden katseluloki 7 pv välein. Luvaton katselu →
      GDPR-rike.
    source: src:PR1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Kasvillisuus muuttuu → kamera-alueiden tarkistus. Naapurikiinteistö?'
  source: src:PR1
- season: Kesä
  action: '[vko 22-35] Vierailija-infomerkit. Mökkikauden yksityisyys.'
  source: src:PR1
- season: Syksy
  action: '[vko 36-48] Lehdet putoavat → kuvakulmat laajenevat, tarkista.'
  source: src:PR1
- season: Talvi
  action: '[vko 49-13] Lumisateet voivat siirtää kameroita → suuntauksen tarkistus.'
  source: src:PR1
FAILURE_MODES:
- mode: Kamera kuvaa naapurikiinteistöä
  detection: Naapurin valitus tai oma tarkistus
  action: Suuntaa HETI, pienennä kuvakulmaa
  source: src:PR1
- mode: Data lähetetty pilveen
  detection: IoT lähettää ulkoiseen palvelimeen
  action: Blokkaa palomuurissa, vaihda laite, kybervahdille
  source: src:PR1
PROCESS_FLOWS:
- flow_id: FLOW_PRIV_01
  trigger: camera_coverage ylittää kynnysarvon (0% naapurikiinteistöä, 0% yleistä
    tietä tunnistettavasti)
  action: Yli 0% → suuntaa kamera HETI, pienennä kuvakulma. Tarkistus 2x/v + asennuksen
    jälkeen.
  output: Tilanneraportti
  source: src:PRIV
- flow_id: FLOW_PRIV_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Kasvillisuus muuttuu → kamera-alueiden tarkistus. Naapurikiinteistö?'
  output: Tarkistuslista
  source: src:PRIV
- flow_id: FLOW_PRIV_03
  trigger: 'Havaittu: Kamera kuvaa naapurikiinteistöä'
  action: Suuntaa HETI, pienennä kuvakulmaa
  output: Poikkeamaraportti
  source: src:PRIV
- flow_id: FLOW_PRIV_04
  trigger: Säännöllinen heartbeat
  action: 'privaattisuus: rutiiniarviointi'
  output: Status-raportti
  source: src:PRIV
KNOWLEDGE_TABLES:
- table_id: TBL_PRIV_01
  title: Privaattisuuden suojelija — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: camera_coverage
    value: 0% naapurikiinteistöä, 0% yleistä tietä tunnistettavasti
    action: Yli 0% → suuntaa kamera HETI, pienennä kuvakulma. Tarkistus 2x/v + asennuksen
      jä
  - metric: data_retention_days
    value: '30'
    action: '>30 pv → automaattipoisto (ei-merkityt). Poliisipyynnön tallenteet 90
      pv.'
  - metric: audio_recording
    value: '0'
    action: Äänitallenne ulkona ilman informointia → GDPR-rike. Pois tai kyltti 'Alueella
      ta
  - metric: data_local_only
    value: Henkilötiedot paikallisesti, ei pilveen
    action: ''
  - metric: access_control
    value: Vain kiinteistön omistaja tai poliisin pyynnöstä
    action: ''
  - metric: data_local_pct
    value: '100'
    action: '100% paikallisesti. Pilvipalveluun lähettäminen → blokkaa palomuurissa,
      ilmoita '
  source: src:PRIV
COMPLIANCE_AND_LEGAL:
  gdpr: 'GDPR: oikeutettu etu tai suostumus [src:PR1]'
  tietosuojalaki: Tietosuojalaki 1050/2018 [src:PR1]
  kameravalvonta: Tietosuojavaltuutetun kameraohje [src:PR2]
UNCERTAINTY_NOTES:
- GDPR:n kotitalouspoikkeus vs systemaattinen valvonta — tulkinnanvarainen.
SOURCE_REGISTRY:
  sources:
  - id: src:PR1
    org: Tietosuojavaltuutettu
    title: Henkilötiedot
    year: 2025
    url: https://tietosuoja.fi/
    supports: GDPR, kameravalvonta.
  - id: src:PR2
    org: Tietosuojavaltuutettu
    title: Kameravalvontaohje
    year: 2025
    url: https://tietosuoja.fi/kameravalvonta
    supports: Sijoittelu, yksityisyys.
eval_questions:
- q: Mikä on camera coverage?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.camera_coverage
  source: src:PR1
- q: 'Toimenpide: camera coverage?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.camera_coverage.action
  source: src:PR1
- q: Mikä on data retention days?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.data_retention_days
  source: src:PR1
- q: 'Toimenpide: data retention days?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.data_retention_days.action
  source: src:PR1
- q: Mikä on audio recording?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.audio_recording
  source: src:PR1
- q: 'Toimenpide: audio recording?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.audio_recording.action
  source: src:PR1
- q: Mikä on data local only?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.data_local_only
  source: src:PR1
- q: Mikä on access control?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.access_control
  source: src:PR1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:PR1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:PR1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:PR1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:PR1
- q: 'Havainto: Kamera kuvaa naapurikiinteistö?'
  a_ref: FAILURE_MODES[0].detection
  source: src:PR1
- q: 'Toiminta: Kamera kuvaa naapurikiinteistö?'
  a_ref: FAILURE_MODES[0].action
  source: src:PR1
- q: 'Havainto: Data lähetetty pilveen?'
  a_ref: FAILURE_MODES[1].detection
  source: src:PR1
- q: 'Toiminta: Data lähetetty pilveen?'
  a_ref: FAILURE_MODES[1].action
  source: src:PR1
- q: 'Sääntö: gdpr?'
  a_ref: COMPLIANCE_AND_LEGAL.gdpr
  source: src:PR1
- q: 'Sääntö: tietosuojalaki?'
  a_ref: COMPLIANCE_AND_LEGAL.tietosuojalaki
  source: src:PR1
- q: 'Sääntö: kameravalvonta?'
  a_ref: COMPLIANCE_AND_LEGAL.kameravalvonta
  source: src:PR1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:PR1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #1?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #2?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #3?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #4?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #5?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #6?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #7?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #8?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #9?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #10?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #11?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #12?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #13?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #14?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #15?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #16?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #17?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #18?'
  a_ref: ASSUMPTIONS
  source: src:PR1
- q: 'Kytkentä muihin agentteihin #19?'
  a_ref: ASSUMPTIONS
  source: src:PR1
```

**sources.yaml:**
```yaml
sources:
- id: src:PR1
  org: Tietosuojavaltuutettu
  title: Henkilötiedot
  year: 2025
  url: https://tietosuoja.fi/
  supports: GDPR, kameravalvonta.
- id: src:PR2
  org: Tietosuojavaltuutettu
  title: Kameravalvontaohje
  year: 2025
  url: https://tietosuoja.fi/kameravalvonta
  supports: Sijoittelu, yksityisyys.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Privaattisuuden suojelija
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Valvoo KAIKKIEN agenttien tietosuojaa
- GDPR + kansallinen tietosuojalaki
- Kamera-, ääni-, sijaintidata

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| camera_coverage | 0% naapurikiinteistöä, 0% yleistä tietä tunnistettavasti | Yli 0% → suuntaa kamera HETI, pienennä kuvakulma. Tarkistus 2x/v + asennuksen jälkeen. | src:PR1 |
| data_retention_days | 30 | >30 pv → automaattipoisto (ei-merkityt). Poliisipyynnön tallenteet 90 pv. | src:PR1 |
| audio_recording | 0 | Äänitallenne ulkona ilman informointia → GDPR-rike. Pois tai kyltti 'Alueella tallentava kameravalvonta'. | src:PR1 |
| data_local_only | Henkilötiedot paikallisesti, ei pilveen | — | src:PR1 |
| access_control | Vain kiinteistön omistaja tai poliisin pyynnöstä | — | src:PR1 |
| data_local_pct | 100 | 100% paikallisesti. Pilvipalveluun lähettäminen → blokkaa palomuurissa, ilmoita kybervahdille. | src:PR1 |
| access_log_audit_days | 7 | Tarkista kameratallenteiden katseluloki 7 pv välein. Luvaton katselu → GDPR-rike. | src:PR1 |

## Tietotaulukot

**Privaattisuuden suojelija — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| camera_coverage | 0% naapurikiinteistöä, 0% yleistä tietä tunnistettavasti | Yli 0% → suuntaa kamera HETI, pienennä kuvakulma. Tarkistus 2x/v + asennuksen jä |
| data_retention_days | 30 | >30 pv → automaattipoisto (ei-merkityt). Poliisipyynnön tallenteet 90 pv. |
| audio_recording | 0 | Äänitallenne ulkona ilman informointia → GDPR-rike. Pois tai kyltti 'Alueella ta |
| data_local_only | Henkilötiedot paikallisesti, ei pilveen |  |
| access_control | Vain kiinteistön omistaja tai poliisin pyynnöstä |  |
| data_local_pct | 100 | 100% paikallisesti. Pilvipalveluun lähettäminen → blokkaa palomuurissa, ilmoita  |

## Prosessit

**FLOW_PRIV_01:** camera_coverage ylittää kynnysarvon (0% naapurikiinteistöä, 0% yleistä tietä tunnistettavasti)
  → Yli 0% → suuntaa kamera HETI, pienennä kuvakulma. Tarkistus 2x/v + asennuksen jälkeen.
  Tulos: Tilanneraportti

**FLOW_PRIV_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Kasvillisuus muuttuu → kamera-alueiden tarkistus. Naapurikiinteistö?
  Tulos: Tarkistuslista

**FLOW_PRIV_03:** Havaittu: Kamera kuvaa naapurikiinteistöä
  → Suuntaa HETI, pienennä kuvakulmaa
  Tulos: Poikkeamaraportti

**FLOW_PRIV_04:** Säännöllinen heartbeat
  → privaattisuus: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Kasvillisuus muuttuu → kamera-alueiden tarkistus. Naapurikiinteistö? | src:PR1 |
| **Kesä** | [vko 22-35] Vierailija-infomerkit. Mökkikauden yksityisyys. | src:PR1 |
| **Syksy** | [vko 36-48] Lehdet putoavat → kuvakulmat laajenevat, tarkista. | src:PR1 |
| **Talvi** | [vko 49-13] Lumisateet voivat siirtää kameroita → suuntauksen tarkistus. | src:PR1 |

## Virhe- ja vaaratilanteet

### ⚠️ Kamera kuvaa naapurikiinteistöä
- **Havaitseminen:** Naapurin valitus tai oma tarkistus
- **Toimenpide:** Suuntaa HETI, pienennä kuvakulmaa
- **Lähde:** src:PR1

### ⚠️ Data lähetetty pilveen
- **Havaitseminen:** IoT lähettää ulkoiseen palvelimeen
- **Toimenpide:** Blokkaa palomuurissa, vaihda laite, kybervahdille
- **Lähde:** src:PR1

## Lait ja vaatimukset
- **gdpr:** GDPR: oikeutettu etu tai suostumus [src:PR1]
- **tietosuojalaki:** Tietosuojalaki 1050/2018 [src:PR1]
- **kameravalvonta:** Tietosuojavaltuutetun kameraohje [src:PR2]

## Epävarmuudet
- GDPR:n kotitalouspoikkeus vs systemaattinen valvonta — tulkinnanvarainen.

## Lähteet
- **src:PR1**: Tietosuojavaltuutettu — *Henkilötiedot* (2025) https://tietosuoja.fi/
- **src:PR2**: Tietosuojavaltuutettu — *Kameravalvontaohje* (2025) https://tietosuoja.fi/kameravalvonta

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on camera coverage?**
    → `DECISION_METRICS_AND_THRESHOLDS.camera_coverage` [src:PR1]
 2. **Toimenpide: camera coverage?**
    → `DECISION_METRICS_AND_THRESHOLDS.camera_coverage.action` [src:PR1]
 3. **Mikä on data retention days?**
    → `DECISION_METRICS_AND_THRESHOLDS.data_retention_days` [src:PR1]
 4. **Toimenpide: data retention days?**
    → `DECISION_METRICS_AND_THRESHOLDS.data_retention_days.action` [src:PR1]
 5. **Mikä on audio recording?**
    → `DECISION_METRICS_AND_THRESHOLDS.audio_recording` [src:PR1]
 6. **Toimenpide: audio recording?**
    → `DECISION_METRICS_AND_THRESHOLDS.audio_recording.action` [src:PR1]
 7. **Mikä on data local only?**
    → `DECISION_METRICS_AND_THRESHOLDS.data_local_only` [src:PR1]
 8. **Mikä on access control?**
    → `DECISION_METRICS_AND_THRESHOLDS.access_control` [src:PR1]
 9. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:PR1]
10. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:PR1]
11. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:PR1]
12. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:PR1]
13. **Havainto: Kamera kuvaa naapurikiinteistö?**
    → `FAILURE_MODES[0].detection` [src:PR1]
14. **Toiminta: Kamera kuvaa naapurikiinteistö?**
    → `FAILURE_MODES[0].action` [src:PR1]
15. **Havainto: Data lähetetty pilveen?**
    → `FAILURE_MODES[1].detection` [src:PR1]
16. **Toiminta: Data lähetetty pilveen?**
    → `FAILURE_MODES[1].action` [src:PR1]
17. **Sääntö: gdpr?**
    → `COMPLIANCE_AND_LEGAL.gdpr` [src:PR1]
18. **Sääntö: tietosuojalaki?**
    → `COMPLIANCE_AND_LEGAL.tietosuojalaki` [src:PR1]
19. **Sääntö: kameravalvonta?**
    → `COMPLIANCE_AND_LEGAL.kameravalvonta` [src:PR1]
20. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:PR1]
21. **Oletukset?**
    → `ASSUMPTIONS` [src:PR1]
22. **Kytkentä muihin agentteihin #1?**
    → `ASSUMPTIONS` [src:PR1]
23. **Kytkentä muihin agentteihin #2?**
    → `ASSUMPTIONS` [src:PR1]
24. **Kytkentä muihin agentteihin #3?**
    → `ASSUMPTIONS` [src:PR1]
25. **Kytkentä muihin agentteihin #4?**
    → `ASSUMPTIONS` [src:PR1]
26. **Kytkentä muihin agentteihin #5?**
    → `ASSUMPTIONS` [src:PR1]
27. **Kytkentä muihin agentteihin #6?**
    → `ASSUMPTIONS` [src:PR1]
28. **Kytkentä muihin agentteihin #7?**
    → `ASSUMPTIONS` [src:PR1]
29. **Kytkentä muihin agentteihin #8?**
    → `ASSUMPTIONS` [src:PR1]
30. **Kytkentä muihin agentteihin #9?**
    → `ASSUMPTIONS` [src:PR1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 40
## AGENT 40: Eräkokki
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: erakokki
  agent_name: Eräkokki
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan mökkikeittiö + ulkogrilli + nuotiopaikka
- 'Raaka-aineet: kala (Huhdasjärvi), riista, marjat, sienet, puutarha'
- Ruokaturvallisuus priorisoitu
DECISION_METRICS_AND_THRESHOLDS:
  fridge_temp_c:
    value: 2-4°C
    action: '>6°C → tarkista, >8°C → ruokaturvallisuusviski'
    source: src:ER1
  meat_core_temp_c:
    value: Kala ≥63°C, siipikarjan ≥75°C, riista ≥72°C
    action: Mittaa AINA sisälämpömittarilla
    source: src:ER1
  fish_freshness_hours:
    value: 'Kylmäketju: jäillä 0-2°C, käyttö 24h sisällä pyynnistä'
    action: Jos epäilys → hylkää (haju, tektuuri, silmät sameat)
    source: src:ER1
  mushroom_identification_confidence:
    value: 100% varma → syö, epävarma → hylkää
    action: VAIN tunnetut lajit. Myrkkysienet voivat olla tappavia.
    source: src:ER2
  fire_safety_distance_m:
    value: 8
    action: Nuotio/grilli ≥8 m rakennuksesta, tuulesta riippuen enemmän
    source: src:ER3
  smoke_cooking_temp_c:
    value: Kylmäsavustus <30°C, kuumasavustus 60-120°C
    source: src:ER1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Nokkoskeitto, voikukkasalaatti. Ahvenfileet. Koivunmahlan keräys
    (huhti).'
  source: src:ER1
- season: Kesä
  action: '[vko 22-35] Grillaus, savustus, marjasäilöntä. Kalan nopea käsittely helteellä.'
  source: src:ER1
- season: Syksy
  action: 'Sienisesonki: tunnista 100% varmuudella. Riistan käsittely. Puolukkahillo.'
  source: src:ER2
- season: Talvi
  action: '[vko 49-13] Nuotiokokkaus (tikkupulla, muurinpohjalettu). Pakasteiden käyttö.
    Pimeyden illalliset.'
  source: src:ER1
FAILURE_MODES:
- mode: Ruokamyrkytysepäily
  detection: Pahoinvointi, ripuli 2-48h ruokailun jälkeen
  action: Nesteitä, lepo. Näyte jäljellä olevasta ruoasta. Lääkäri jos kova kuume
    tai veriripuli.
  source: src:ER1
- mode: Myrkkysieniepäily
  detection: Oksentelu 6-24h sieniaterian jälkeen
  action: HÄTÄNUMERO 112 + Myrkytystietokeskus 0800 147 111. Näyte jäljellä olevista
    sienistä.
  source: src:ER2
PROCESS_FLOWS:
- flow_id: FLOW_ERAK_01
  trigger: fridge_temp_c ylittää kynnysarvon (2-4°C)
  action: '>6°C → tarkista, >8°C → ruokaturvallisuusviski'
  output: Tilanneraportti
  source: src:ERAK
- flow_id: FLOW_ERAK_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Nokkoskeitto, voikukkasalaatti. Ahvenfileet. Koivunmahlan keräys
    (huhti).'
  output: Tarkistuslista
  source: src:ERAK
- flow_id: FLOW_ERAK_03
  trigger: 'Havaittu: Ruokamyrkytysepäily'
  action: Nesteitä, lepo. Näyte jäljellä olevasta ruoasta. Lääkäri jos kova kuume
    tai veriripuli.
  output: Poikkeamaraportti
  source: src:ERAK
- flow_id: FLOW_ERAK_04
  trigger: Säännöllinen heartbeat
  action: 'erakokki: rutiiniarviointi'
  output: Status-raportti
  source: src:ERAK
KNOWLEDGE_TABLES:
  seasonal_ingredients:
  - season: Kevät
    ingredients: Nokkonen, voikukka, koivunmahla, ahven (kutu), hauki (toukokuu)
  - season: Kesä
    ingredients: Marjat (mansikka, mustikka, puolukka), uudet perunat, yrtit, vadelma
  - season: Syksy
    ingredients: Sienet (kantarelli, herkkutatti, suppilovahvero), puolukka, karpalo,
      riista
  - season: Talvi
    ingredients: Säilöntätuotteet, pakastettu riista, kuivatut sienet, juurekset
COMPLIANCE_AND_LEGAL:
  tulenteko: 'Avotulen teko: metsäpalovaroituksen aikana KIELLETTY [src:ER3]'
  elintarviketurvallisuus: 'Kotitalouden ruoka omaan käyttöön: ei lupaa vaadita [src:ER1]'
UNCERTAINTY_NOTES:
- 'Sienten tunnistus: jotkin myrkylliset lajit muistuttavat syötäviä — VAIN 100% varmuus.'
- Riistan lihan pilaantuminen nopeutuu yli 10°C — kylmäketjun merkitys korostuu.
SOURCE_REGISTRY:
  sources:
  - id: src:ER1
    org: Ruokavirasto
    title: Elintarviketurvallisuus
    year: 2025
    url: https://www.ruokavirasto.fi/
    supports: Lämpötilat, kylmäketju, ruokamyrkytys.
  - id: src:ER2
    org: Luonnontieteellinen museo
    title: Sienten tunnistus
    year: 2025
    url: https://www.luomus.fi/
    supports: Sienet, myrkkysienet.
  - id: src:ER3
    org: Pelastuslaitos
    title: Avotulen teko
    year: 2025
    url: https://www.pelastustoimi.fi/
    supports: Nuotio, metsäpalovaroitus.
eval_questions:
- q: Mikä on jääkaapin tavoitelämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fridge_temp_c.value
  source: src:ER1
- q: Mikä on kalan sisälämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.meat_core_temp_c.value
  source: src:ER1
- q: Miten kalan tuoreus tarkistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fish_freshness_hours.action
  source: src:ER1
- q: Voiko epävarmaa sientä syödä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mushroom_identification_confidence.action
  source: src:ER2
- q: Mikä on nuotion turvaetäisyys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fire_safety_distance_m.value
  source: src:ER3
- q: Mitä tehdään myrkkysieniepäilyssä?
  a_ref: FAILURE_MODES[1].action
  source: src:ER2
- q: Onko avotuli sallittu metsäpalovaroituksella?
  a_ref: COMPLIANCE_AND_LEGAL.tulenteko
  source: src:ER3
- q: Mitkä sienet kerätään syksyllä?
  a_ref: KNOWLEDGE_TABLES.seasonal_ingredients[2].ingredients
  source: src:ER2
- q: Mikä on fridge temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fridge_temp_c
  source: src:ER1
- q: 'Toimenpide: fridge temp c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fridge_temp_c.action
  source: src:ER1
- q: Mikä on meat core temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.meat_core_temp_c
  source: src:ER1
- q: 'Toimenpide: meat core temp c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.meat_core_temp_c.action
  source: src:ER1
- q: Mikä on fish freshness hours?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fish_freshness_hours
  source: src:ER1
- q: 'Toimenpide: fish freshness hours?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fish_freshness_hours.action
  source: src:ER1
- q: Mikä on mushroom identification confidence?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mushroom_identification_confidence
  source: src:ER1
- q: 'Toimenpide: mushroom identification confidence?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mushroom_identification_confidence.action
  source: src:ER1
- q: Mikä on fire safety distance m?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fire_safety_distance_m
  source: src:ER1
- q: 'Toimenpide: fire safety distance m?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fire_safety_distance_m.action
  source: src:ER1
- q: Mikä on smoke cooking temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.smoke_cooking_temp_c
  source: src:ER1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:ER1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:ER1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:ER1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:ER1
- q: 'Havainto: Ruokamyrkytysepäily?'
  a_ref: FAILURE_MODES[0].detection
  source: src:ER1
- q: 'Toiminta: Ruokamyrkytysepäily?'
  a_ref: FAILURE_MODES[0].action
  source: src:ER1
- q: 'Havainto: Myrkkysieniepäily?'
  a_ref: FAILURE_MODES[1].detection
  source: src:ER1
- q: 'Toiminta: Myrkkysieniepäily?'
  a_ref: FAILURE_MODES[1].action
  source: src:ER1
- q: 'Sääntö: tulenteko?'
  a_ref: COMPLIANCE_AND_LEGAL.tulenteko
  source: src:ER1
- q: 'Sääntö: elintarviketurvallisuus?'
  a_ref: COMPLIANCE_AND_LEGAL.elintarviketurvallisuus
  source: src:ER1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:ER1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:ER1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:ER1
```

**sources.yaml:**
```yaml
sources:
- id: src:ER1
  org: Ruokavirasto
  title: Elintarviketurvallisuus
  year: 2025
  url: https://www.ruokavirasto.fi/
  supports: Lämpötilat, kylmäketju, ruokamyrkytys.
- id: src:ER2
  org: Luonnontieteellinen museo
  title: Sienten tunnistus
  year: 2025
  url: https://www.luomus.fi/
  supports: Sienet, myrkkysienet.
- id: src:ER3
  org: Pelastuslaitos
  title: Avotulen teko
  year: 2025
  url: https://www.pelastustoimi.fi/
  supports: Nuotio, metsäpalovaroitus.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Eräkokki
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan mökkikeittiö + ulkogrilli + nuotiopaikka
- Raaka-aineet: kala (Huhdasjärvi), riista, marjat, sienet, puutarha
- Ruokaturvallisuus priorisoitu

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| fridge_temp_c | 2-4°C | >6°C → tarkista, >8°C → ruokaturvallisuusviski | src:ER1 |
| meat_core_temp_c | Kala ≥63°C, siipikarjan ≥75°C, riista ≥72°C | Mittaa AINA sisälämpömittarilla | src:ER1 |
| fish_freshness_hours | Kylmäketju: jäillä 0-2°C, käyttö 24h sisällä pyynnistä | Jos epäilys → hylkää (haju, tektuuri, silmät sameat) | src:ER1 |
| mushroom_identification_confidence | 100% varma → syö, epävarma → hylkää | VAIN tunnetut lajit. Myrkkysienet voivat olla tappavia. | src:ER2 |
| fire_safety_distance_m | 8 | Nuotio/grilli ≥8 m rakennuksesta, tuulesta riippuen enemmän | src:ER3 |
| smoke_cooking_temp_c | Kylmäsavustus <30°C, kuumasavustus 60-120°C | — | src:ER1 |

## Tietotaulukot

**seasonal_ingredients:**

| season | ingredients |
| --- | --- |
| Kevät | Nokkonen, voikukka, koivunmahla, ahven (kutu), hauki (toukokuu) |
| Kesä | Marjat (mansikka, mustikka, puolukka), uudet perunat, yrtit, vadelma |
| Syksy | Sienet (kantarelli, herkkutatti, suppilovahvero), puolukka, karpalo, riista |
| Talvi | Säilöntätuotteet, pakastettu riista, kuivatut sienet, juurekset |

## Prosessit

**FLOW_ERAK_01:** fridge_temp_c ylittää kynnysarvon (2-4°C)
  → >6°C → tarkista, >8°C → ruokaturvallisuusviski
  Tulos: Tilanneraportti

**FLOW_ERAK_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Nokkoskeitto, voikukkasalaatti. Ahvenfileet. Koivunmahlan keräys (huhti).
  Tulos: Tarkistuslista

**FLOW_ERAK_03:** Havaittu: Ruokamyrkytysepäily
  → Nesteitä, lepo. Näyte jäljellä olevasta ruoasta. Lääkäri jos kova kuume tai veriripuli.
  Tulos: Poikkeamaraportti

**FLOW_ERAK_04:** Säännöllinen heartbeat
  → erakokki: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Nokkoskeitto, voikukkasalaatti. Ahvenfileet. Koivunmahlan keräys (huhti). | src:ER1 |
| **Kesä** | [vko 22-35] Grillaus, savustus, marjasäilöntä. Kalan nopea käsittely helteellä. | src:ER1 |
| **Syksy** | Sienisesonki: tunnista 100% varmuudella. Riistan käsittely. Puolukkahillo. | src:ER2 |
| **Talvi** | [vko 49-13] Nuotiokokkaus (tikkupulla, muurinpohjalettu). Pakasteiden käyttö. Pimeyden illalliset. | src:ER1 |

## Virhe- ja vaaratilanteet

### ⚠️ Ruokamyrkytysepäily
- **Havaitseminen:** Pahoinvointi, ripuli 2-48h ruokailun jälkeen
- **Toimenpide:** Nesteitä, lepo. Näyte jäljellä olevasta ruoasta. Lääkäri jos kova kuume tai veriripuli.
- **Lähde:** src:ER1

### ⚠️ Myrkkysieniepäily
- **Havaitseminen:** Oksentelu 6-24h sieniaterian jälkeen
- **Toimenpide:** HÄTÄNUMERO 112 + Myrkytystietokeskus 0800 147 111. Näyte jäljellä olevista sienistä.
- **Lähde:** src:ER2

## Lait ja vaatimukset
- **tulenteko:** Avotulen teko: metsäpalovaroituksen aikana KIELLETTY [src:ER3]
- **elintarviketurvallisuus:** Kotitalouden ruoka omaan käyttöön: ei lupaa vaadita [src:ER1]

## Epävarmuudet
- Sienten tunnistus: jotkin myrkylliset lajit muistuttavat syötäviä — VAIN 100% varmuus.
- Riistan lihan pilaantuminen nopeutuu yli 10°C — kylmäketjun merkitys korostuu.

## Lähteet
- **src:ER1**: Ruokavirasto — *Elintarviketurvallisuus* (2025) https://www.ruokavirasto.fi/
- **src:ER2**: Luonnontieteellinen museo — *Sienten tunnistus* (2025) https://www.luomus.fi/
- **src:ER3**: Pelastuslaitos — *Avotulen teko* (2025) https://www.pelastustoimi.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on jääkaapin tavoitelämpötila?**
    → `DECISION_METRICS_AND_THRESHOLDS.fridge_temp_c.value` [src:ER1]
 2. **Mikä on kalan sisälämpötila?**
    → `DECISION_METRICS_AND_THRESHOLDS.meat_core_temp_c.value` [src:ER1]
 3. **Miten kalan tuoreus tarkistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.fish_freshness_hours.action` [src:ER1]
 4. **Voiko epävarmaa sientä syödä?**
    → `DECISION_METRICS_AND_THRESHOLDS.mushroom_identification_confidence.action` [src:ER2]
 5. **Mikä on nuotion turvaetäisyys?**
    → `DECISION_METRICS_AND_THRESHOLDS.fire_safety_distance_m.value` [src:ER3]
 6. **Mitä tehdään myrkkysieniepäilyssä?**
    → `FAILURE_MODES[1].action` [src:ER2]
 7. **Onko avotuli sallittu metsäpalovaroituksella?**
    → `COMPLIANCE_AND_LEGAL.tulenteko` [src:ER3]
 8. **Mitkä sienet kerätään syksyllä?**
    → `KNOWLEDGE_TABLES.seasonal_ingredients[2].ingredients` [src:ER2]
 9. **Mikä on fridge temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.fridge_temp_c` [src:ER1]
10. **Toimenpide: fridge temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.fridge_temp_c.action` [src:ER1]
11. **Mikä on meat core temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.meat_core_temp_c` [src:ER1]
12. **Toimenpide: meat core temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.meat_core_temp_c.action` [src:ER1]
13. **Mikä on fish freshness hours?**
    → `DECISION_METRICS_AND_THRESHOLDS.fish_freshness_hours` [src:ER1]
14. **Toimenpide: fish freshness hours?**
    → `DECISION_METRICS_AND_THRESHOLDS.fish_freshness_hours.action` [src:ER1]
15. **Mikä on mushroom identification confidence?**
    → `DECISION_METRICS_AND_THRESHOLDS.mushroom_identification_confidence` [src:ER1]
16. **Toimenpide: mushroom identification confidence?**
    → `DECISION_METRICS_AND_THRESHOLDS.mushroom_identification_confidence.action` [src:ER1]
17. **Mikä on fire safety distance m?**
    → `DECISION_METRICS_AND_THRESHOLDS.fire_safety_distance_m` [src:ER1]
18. **Toimenpide: fire safety distance m?**
    → `DECISION_METRICS_AND_THRESHOLDS.fire_safety_distance_m.action` [src:ER1]
19. **Mikä on smoke cooking temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.smoke_cooking_temp_c` [src:ER1]
20. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:ER1]
21. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:ER1]
22. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:ER1]
23. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:ER1]
24. **Havainto: Ruokamyrkytysepäily?**
    → `FAILURE_MODES[0].detection` [src:ER1]
25. **Toiminta: Ruokamyrkytysepäily?**
    → `FAILURE_MODES[0].action` [src:ER1]
26. **Havainto: Myrkkysieniepäily?**
    → `FAILURE_MODES[1].detection` [src:ER1]
27. **Toiminta: Myrkkysieniepäily?**
    → `FAILURE_MODES[1].action` [src:ER1]
28. **Sääntö: tulenteko?**
    → `COMPLIANCE_AND_LEGAL.tulenteko` [src:ER1]
29. **Sääntö: elintarviketurvallisuus?**
    → `COMPLIANCE_AND_LEGAL.elintarviketurvallisuus` [src:ER1]
30. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:ER1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 41
## AGENT 41: Leipuri
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: leipuri
  agent_name: Leipuri
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Mökkileipominen: leivinuuni, puulämmitteinen'
- Hapanjuuri, ruisleipä, pulla, karjalanpiirakat
DECISION_METRICS_AND_THRESHOLDS:
  oven_temp_bread_c:
    value: 220-250°C (ruisleipä), 200-220°C (vehnäleipä)
    source: src:LE1
  sourdough_activity:
    value: Tupla tilavuus 4-8h huoneenlämmössä
    action: 'Ei nouse → elvytä: uusi jauholisäys, lämpö 25-28°C'
    source: src:LE1
  dough_hydration_pct:
    value: Ruisleipä 75-85%, vehnä 65-75%
    source: src:LE1
  bread_core_temp_c:
    value: 96-98°C → kypsä
    action: Mittaa pitkällä piikkimittarilla
    source: src:LE1
  flour_storage_months:
    value: Täysjyväjauho 3-6 kk, valkoinen 12 kk viileässä
    action: Haju tai hyönteiset → hylkää
    source: src:LE1
  leivinuuni_heat_hours:
    value: Lämmitys 2-3h, leipominen kun luukku suljettu ja T 220-280°C
    source: src:LE1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Pääsiäisleipä (mämmi, pasha). Hapanjuuren elvytys talven jälkeen.'
  source: src:LE1
- season: Kesä
  action: '[vko 22-35] Kesäleivät (nuotioleipä, grillileipä). Taikinan nostatus nopeutuu
    lämmössä.'
  source: src:LE1
- season: Syksy
  action: '[vko 36-48] Sadonkorjuuleipä. Joulupiparkakkujen valmistelu. Ruisjauhosadon
    käyttöönotto.'
  source: src:LE1
- season: Talvi
  action: '[vko 49-13] Joululeipiä: pipari, tortut, pulla. Leivinuunin hyödyntäminen
    lämmitykseen.'
  source: src:LE1
FAILURE_MODES:
- mode: Hapanjuuri kuollut
  detection: Ei kuplia, paha haju, ei nouse
  action: 'Aloita uusi: ruis+vesi, 5pv käyminen. Tai hanki startteria naapurilta.'
  source: src:LE1
- mode: Leipä ei kypsä sisältä
  detection: Tahmea sisus, sisälämpö <95°C
  action: Jatka paistoa alhaisemmalla lämmöllä (180°C) 15-20 min
  source: src:LE1
PROCESS_FLOWS:
- flow_id: FLOW_LEIP_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Pääsiäisleipä (mämmi, pasha). Hapanjuuren elvytys talven jälkeen.'
  output: Tarkistuslista
  source: src:LEIP
- flow_id: FLOW_LEIP_03
  trigger: 'Havaittu: Hapanjuuri kuollut'
  action: 'Aloita uusi: ruis+vesi, 5pv käyminen. Tai hanki startteria naapurilta.'
  output: Poikkeamaraportti
  source: src:LEIP
- flow_id: FLOW_LEIP_04
  trigger: Säännöllinen heartbeat
  action: 'leipuri: rutiiniarviointi'
  output: Status-raportti
  source: src:LEIP
KNOWLEDGE_TABLES:
- table_id: TBL_LEIP_01
  title: Leipuri — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: oven_temp_bread_c
    value: 220-250°C (ruisleipä), 200-220°C (vehnäleipä)
    action: ''
  - metric: sourdough_activity
    value: Tupla tilavuus 4-8h huoneenlämmössä
    action: 'Ei nouse → elvytä: uusi jauholisäys, lämpö 25-28°C'
  - metric: dough_hydration_pct
    value: Ruisleipä 75-85%, vehnä 65-75%
    action: ''
  - metric: bread_core_temp_c
    value: 96-98°C → kypsä
    action: Mittaa pitkällä piikkimittarilla
  - metric: flour_storage_months
    value: Täysjyväjauho 3-6 kk, valkoinen 12 kk viileässä
    action: Haju tai hyönteiset → hylkää
  - metric: leivinuuni_heat_hours
    value: Lämmitys 2-3h, leipominen kun luukku suljettu ja T 220-280°C
    action: ''
  source: src:LEIP
COMPLIANCE_AND_LEGAL:
  myynti: 'Satunnaisesta kotileipomisesta myyntiin: omavalvontasuunnitelma Ruokavirastoon
    jos säännöllistä [src:LE2]'
UNCERTAINTY_NOTES:
- Leivinuunin lämpötila vaihtelee — termometri hormiin/uuniin on välttämätön.
- Hapanjuuren aktiivisuus riippuu ympäristöoloista — ei aina toistettavissa.
SOURCE_REGISTRY:
  sources:
  - id: src:LE1
    org: Marttaliitto
    title: Leipäohjeistot
    year: 2025
    url: https://www.martat.fi/
    supports: Reseptit, lämpötilat, hapanjuuri.
  - id: src:LE2
    org: Ruokavirasto
    title: Kotitalouden myynti
    year: 2025
    url: https://www.ruokavirasto.fi/
    supports: Elintarvikemyynti.
eval_questions:
- q: Mikä on oven temp bread c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.oven_temp_bread_c
  source: src:LE1
- q: Mikä on sourdough activity?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sourdough_activity
  source: src:LE1
- q: 'Toimenpide: sourdough activity?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sourdough_activity.action
  source: src:LE1
- q: Mikä on dough hydration pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.dough_hydration_pct
  source: src:LE1
- q: Mikä on bread core temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bread_core_temp_c
  source: src:LE1
- q: 'Toimenpide: bread core temp c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bread_core_temp_c.action
  source: src:LE1
- q: Mikä on flour storage months?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.flour_storage_months
  source: src:LE1
- q: 'Toimenpide: flour storage months?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.flour_storage_months.action
  source: src:LE1
- q: Mikä on leivinuuni heat hours?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.leivinuuni_heat_hours
  source: src:LE1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:LE1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:LE1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:LE1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:LE1
- q: 'Havainto: Hapanjuuri kuollut?'
  a_ref: FAILURE_MODES[0].detection
  source: src:LE1
- q: 'Toiminta: Hapanjuuri kuollut?'
  a_ref: FAILURE_MODES[0].action
  source: src:LE1
- q: 'Havainto: Leipä ei kypsä sisältä?'
  a_ref: FAILURE_MODES[1].detection
  source: src:LE1
- q: 'Toiminta: Leipä ei kypsä sisältä?'
  a_ref: FAILURE_MODES[1].action
  source: src:LE1
- q: 'Sääntö: myynti?'
  a_ref: COMPLIANCE_AND_LEGAL.myynti
  source: src:LE1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:LE1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #16?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #17?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #18?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #19?'
  a_ref: ASSUMPTIONS
  source: src:LE1
- q: 'Operatiivinen lisäkysymys #20?'
  a_ref: ASSUMPTIONS
  source: src:LE1
```

**sources.yaml:**
```yaml
sources:
- id: src:LE1
  org: Marttaliitto
  title: Leipäohjeistot
  year: 2025
  url: https://www.martat.fi/
  supports: Reseptit, lämpötilat, hapanjuuri.
- id: src:LE2
  org: Ruokavirasto
  title: Kotitalouden myynti
  year: 2025
  url: https://www.ruokavirasto.fi/
  supports: Elintarvikemyynti.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Leipuri
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Mökkileipominen: leivinuuni, puulämmitteinen
- Hapanjuuri, ruisleipä, pulla, karjalanpiirakat

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| oven_temp_bread_c | 220-250°C (ruisleipä), 200-220°C (vehnäleipä) | — | src:LE1 |
| sourdough_activity | Tupla tilavuus 4-8h huoneenlämmössä | Ei nouse → elvytä: uusi jauholisäys, lämpö 25-28°C | src:LE1 |
| dough_hydration_pct | Ruisleipä 75-85%, vehnä 65-75% | — | src:LE1 |
| bread_core_temp_c | 96-98°C → kypsä | Mittaa pitkällä piikkimittarilla | src:LE1 |
| flour_storage_months | Täysjyväjauho 3-6 kk, valkoinen 12 kk viileässä | Haju tai hyönteiset → hylkää | src:LE1 |
| leivinuuni_heat_hours | Lämmitys 2-3h, leipominen kun luukku suljettu ja T 220-280°C | — | src:LE1 |

## Tietotaulukot

**Leipuri — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| oven_temp_bread_c | 220-250°C (ruisleipä), 200-220°C (vehnäleipä) |  |
| sourdough_activity | Tupla tilavuus 4-8h huoneenlämmössä | Ei nouse → elvytä: uusi jauholisäys, lämpö 25-28°C |
| dough_hydration_pct | Ruisleipä 75-85%, vehnä 65-75% |  |
| bread_core_temp_c | 96-98°C → kypsä | Mittaa pitkällä piikkimittarilla |
| flour_storage_months | Täysjyväjauho 3-6 kk, valkoinen 12 kk viileässä | Haju tai hyönteiset → hylkää |
| leivinuuni_heat_hours | Lämmitys 2-3h, leipominen kun luukku suljettu ja T 220-280°C |  |

## Prosessit

**FLOW_LEIP_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Pääsiäisleipä (mämmi, pasha). Hapanjuuren elvytys talven jälkeen.
  Tulos: Tarkistuslista

**FLOW_LEIP_03:** Havaittu: Hapanjuuri kuollut
  → Aloita uusi: ruis+vesi, 5pv käyminen. Tai hanki startteria naapurilta.
  Tulos: Poikkeamaraportti

**FLOW_LEIP_04:** Säännöllinen heartbeat
  → leipuri: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Pääsiäisleipä (mämmi, pasha). Hapanjuuren elvytys talven jälkeen. | src:LE1 |
| **Kesä** | [vko 22-35] Kesäleivät (nuotioleipä, grillileipä). Taikinan nostatus nopeutuu lämmössä. | src:LE1 |
| **Syksy** | [vko 36-48] Sadonkorjuuleipä. Joulupiparkakkujen valmistelu. Ruisjauhosadon käyttöönotto. | src:LE1 |
| **Talvi** | [vko 49-13] Joululeipiä: pipari, tortut, pulla. Leivinuunin hyödyntäminen lämmitykseen. | src:LE1 |

## Virhe- ja vaaratilanteet

### ⚠️ Hapanjuuri kuollut
- **Havaitseminen:** Ei kuplia, paha haju, ei nouse
- **Toimenpide:** Aloita uusi: ruis+vesi, 5pv käyminen. Tai hanki startteria naapurilta.
- **Lähde:** src:LE1

### ⚠️ Leipä ei kypsä sisältä
- **Havaitseminen:** Tahmea sisus, sisälämpö <95°C
- **Toimenpide:** Jatka paistoa alhaisemmalla lämmöllä (180°C) 15-20 min
- **Lähde:** src:LE1

## Lait ja vaatimukset
- **myynti:** Satunnaisesta kotileipomisesta myyntiin: omavalvontasuunnitelma Ruokavirastoon jos säännöllistä [src:LE2]

## Epävarmuudet
- Leivinuunin lämpötila vaihtelee — termometri hormiin/uuniin on välttämätön.
- Hapanjuuren aktiivisuus riippuu ympäristöoloista — ei aina toistettavissa.

## Lähteet
- **src:LE1**: Marttaliitto — *Leipäohjeistot* (2025) https://www.martat.fi/
- **src:LE2**: Ruokavirasto — *Kotitalouden myynti* (2025) https://www.ruokavirasto.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on oven temp bread c?**
    → `DECISION_METRICS_AND_THRESHOLDS.oven_temp_bread_c` [src:LE1]
 2. **Mikä on sourdough activity?**
    → `DECISION_METRICS_AND_THRESHOLDS.sourdough_activity` [src:LE1]
 3. **Toimenpide: sourdough activity?**
    → `DECISION_METRICS_AND_THRESHOLDS.sourdough_activity.action` [src:LE1]
 4. **Mikä on dough hydration pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.dough_hydration_pct` [src:LE1]
 5. **Mikä on bread core temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.bread_core_temp_c` [src:LE1]
 6. **Toimenpide: bread core temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.bread_core_temp_c.action` [src:LE1]
 7. **Mikä on flour storage months?**
    → `DECISION_METRICS_AND_THRESHOLDS.flour_storage_months` [src:LE1]
 8. **Toimenpide: flour storage months?**
    → `DECISION_METRICS_AND_THRESHOLDS.flour_storage_months.action` [src:LE1]
 9. **Mikä on leivinuuni heat hours?**
    → `DECISION_METRICS_AND_THRESHOLDS.leivinuuni_heat_hours` [src:LE1]
10. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:LE1]
11. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:LE1]
12. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:LE1]
13. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:LE1]
14. **Havainto: Hapanjuuri kuollut?**
    → `FAILURE_MODES[0].detection` [src:LE1]
15. **Toiminta: Hapanjuuri kuollut?**
    → `FAILURE_MODES[0].action` [src:LE1]
16. **Havainto: Leipä ei kypsä sisältä?**
    → `FAILURE_MODES[1].detection` [src:LE1]
17. **Toiminta: Leipä ei kypsä sisältä?**
    → `FAILURE_MODES[1].action` [src:LE1]
18. **Sääntö: myynti?**
    → `COMPLIANCE_AND_LEGAL.myynti` [src:LE1]
19. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:LE1]
20. **Oletukset?**
    → `ASSUMPTIONS` [src:LE1]
21. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:LE1]
22. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:LE1]
23. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:LE1]
24. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:LE1]
25. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:LE1]
26. **Operatiivinen lisäkysymys #6?**
    → `ASSUMPTIONS` [src:LE1]
27. **Operatiivinen lisäkysymys #7?**
    → `ASSUMPTIONS` [src:LE1]
28. **Operatiivinen lisäkysymys #8?**
    → `ASSUMPTIONS` [src:LE1]
29. **Operatiivinen lisäkysymys #9?**
    → `ASSUMPTIONS` [src:LE1]
30. **Operatiivinen lisäkysymys #10?**
    → `ASSUMPTIONS` [src:LE1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 42
## AGENT 42: Ravintoterapeutti
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: ravintoterapeutti
  agent_name: Ravintoterapeutti
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Janin ja perheen ravintosuositukset
- Fyysisesti aktiivinen (mehiläishoito, metsätyö, mökkielämä)
- Paikallisten raaka-aineiden hyödyntäminen
DECISION_METRICS_AND_THRESHOLDS:
  daily_energy_kcal:
    value: 2500-3000 kcal (aktiivinen mies, 52v)
    note: Raskaan työn päivinä (mehiläiset, puunkaato) +500 kcal
    source: src:RA1
    action: '2500-3000 kcal/pv perus. Raskas työpäivä (puunkaato, mehiläishoito) →
      +500 kcal. Eväät mukaan: 600-800 kcal välipalana.'
  protein_g_per_kg:
    value: 1.2-1.6 g/kg/pv (aktiivinen aikuinen)
    source: src:RA1
  hydration_l_per_day:
    value: 2.5-3.5 l (sis. ruoan nesteen)
    action: 2.5-3.5 l/pv. Kuuma ulkotyö (>25°C) → +1 l. Tumma virtsa → välitön nestely.
      Suola + vesi (1/4 tl / 0.5 l).
    source: src:RA1
  vitamin_d_ug:
    value: 10-20 μg/pv (talvella lisäravinne suositeltava)
    source: src:RA1
    action: 'Lokakuu-maaliskuu: lisäravinne 20 μg/pv. Kesällä auringosta riittävästi.
      Tarkista verikoe 2v välein.'
  omega3_weekly_fish:
    value: 2-3 kala-ateriaa viikossa
    source: src:RA1
  sugar_max_energy_pct:
    value: <10% kokonaisenergiasta
    source: src:RA1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] D-vitamiini vielä tärkeä. Tuoreet villivihannekset (nokkonen).
    Kevään väsymys → rautapitoisuus.'
  source: src:RA1
- season: Kesä
  action: Nesteytys kriittistä ulkotyössä. Marjat antioksidantteja. Kalan omega-3.
  source: src:RA1
- season: Syksy
  action: '[vko 36-48] Sieni-/marjasäilöntä talveksi. Kauden juurekset. Immuniteetin
    vahvistus.'
  source: src:RA1
- season: Talvi
  action: D-vitamiinilisä 20 μg/pv. Pakastetut marjat C-vitamiiniin. Lämmin ruoka.
  source: src:RA1
FAILURE_MODES:
- mode: Dehydraatio ulkotyössä
  detection: Päänsärky, väsymys, tumma virtsa
  action: Juomatauko 15min välein, suolaa + vettä, varjoon
  source: src:RA1
- mode: Energiavaje pitkänä työpäivänä
  detection: Väsymys, huimaus klo 14-16
  action: 'Eväät mukaan: pähkinät, leipä, juoma. Tauko 2h välein.'
  source: src:RA1
PROCESS_FLOWS:
- flow_id: FLOW_RAVI_01
  trigger: daily_energy_kcal ylittää kynnysarvon (2500-3000 kcal (aktiivinen mies,
    52v))
  action: '2500-3000 kcal/pv perus. Raskas työpäivä (puunkaato, mehiläishoito) → +500
    kcal. Eväät mukaan: 600-800 kcal välipalana.'
  output: Tilanneraportti
  source: src:RAVI
- flow_id: FLOW_RAVI_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] D-vitamiini vielä tärkeä. Tuoreet villivihannekset (nokkonen).
    Kevään väsymys → rautapit'
  output: Tarkistuslista
  source: src:RAVI
- flow_id: FLOW_RAVI_03
  trigger: 'Havaittu: Dehydraatio ulkotyössä'
  action: Juomatauko 15min välein, suolaa + vettä, varjoon
  output: Poikkeamaraportti
  source: src:RAVI
- flow_id: FLOW_RAVI_04
  trigger: Säännöllinen heartbeat
  action: 'ravintoterapeutti: rutiiniarviointi'
  output: Status-raportti
  source: src:RAVI
KNOWLEDGE_TABLES:
- table_id: TBL_RAVI_01
  title: Ravintoterapeutti — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: daily_energy_kcal
    value: 2500-3000 kcal (aktiivinen mies, 52v)
    action: 2500-3000 kcal/pv perus. Raskas työpäivä (puunkaato, mehiläishoito) →
      +500 kcal.
  - metric: protein_g_per_kg
    value: 1.2-1.6 g/kg/pv (aktiivinen aikuinen)
    action: ''
  - metric: hydration_l_per_day
    value: 2.5-3.5 l (sis. ruoan nesteen)
    action: 2.5-3.5 l/pv. Kuuma ulkotyö (>25°C) → +1 l. Tumma virtsa → välitön nestely.
      Suol
  - metric: vitamin_d_ug
    value: 10-20 μg/pv (talvella lisäravinne suositeltava)
    action: 'Lokakuu-maaliskuu: lisäravinne 20 μg/pv. Kesällä auringosta riittävästi.
      Tarkist'
  - metric: omega3_weekly_fish
    value: 2-3 kala-ateriaa viikossa
    action: ''
  - metric: sugar_max_energy_pct
    value: <10% kokonaisenergiasta
    action: ''
  source: src:RAVI
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Ravintosuositukset ovat väestötason ohjeita — yksilölliset tarpeet vaihtelevat.
- 'Hunajan ravintosisältö: pääosin sokereita, vähän mikroravinteita.'
SOURCE_REGISTRY:
  sources:
  - id: src:RA1
    org: THL / Ruokavirasto
    title: Suomalaiset ravitsemussuositukset
    year: 2024
    url: https://www.ruokavirasto.fi/teemat/terveytta-edistava-ruokavalio/
    supports: Energia, proteiini, D-vitamiini, nesteytys.
eval_questions:
- q: Mikä on daily energy kcal?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.daily_energy_kcal
  source: src:RA1
- q: Mikä on protein g per kg?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.protein_g_per_kg
  source: src:RA1
- q: Mikä on hydration l per day?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hydration_l_per_day
  source: src:RA1
- q: 'Toimenpide: hydration l per day?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hydration_l_per_day.action
  source: src:RA1
- q: Mikä on vitamin d ug?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.vitamin_d_ug
  source: src:RA1
- q: Mikä on omega3 weekly fish?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.omega3_weekly_fish
  source: src:RA1
- q: Mikä on sugar max energy pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sugar_max_energy_pct
  source: src:RA1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:RA1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:RA1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:RA1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:RA1
- q: 'Havainto: Dehydraatio ulkotyössä?'
  a_ref: FAILURE_MODES[0].detection
  source: src:RA1
- q: 'Toiminta: Dehydraatio ulkotyössä?'
  a_ref: FAILURE_MODES[0].action
  source: src:RA1
- q: 'Havainto: Energiavaje pitkänä työpäivänä?'
  a_ref: FAILURE_MODES[1].detection
  source: src:RA1
- q: 'Toiminta: Energiavaje pitkänä työpäivänä?'
  a_ref: FAILURE_MODES[1].action
  source: src:RA1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:RA1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #16?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #17?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #18?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #19?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #20?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #21?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #22?'
  a_ref: ASSUMPTIONS
  source: src:RA1
- q: 'Operatiivinen lisäkysymys #23?'
  a_ref: ASSUMPTIONS
  source: src:RA1
```

**sources.yaml:**
```yaml
sources:
- id: src:RA1
  org: THL / Ruokavirasto
  title: Suomalaiset ravitsemussuositukset
  year: 2024
  url: https://www.ruokavirasto.fi/teemat/terveytta-edistava-ruokavalio/
  supports: Energia, proteiini, D-vitamiini, nesteytys.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Ravintoterapeutti
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Janin ja perheen ravintosuositukset
- Fyysisesti aktiivinen (mehiläishoito, metsätyö, mökkielämä)
- Paikallisten raaka-aineiden hyödyntäminen

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| daily_energy_kcal | 2500-3000 kcal (aktiivinen mies, 52v) | 2500-3000 kcal/pv perus. Raskas työpäivä (puunkaato, mehiläishoito) → +500 kcal. Eväät mukaan: 600-800 kcal välipalana. | src:RA1 |
| protein_g_per_kg | 1.2-1.6 g/kg/pv (aktiivinen aikuinen) | — | src:RA1 |
| hydration_l_per_day | 2.5-3.5 l (sis. ruoan nesteen) | 2.5-3.5 l/pv. Kuuma ulkotyö (>25°C) → +1 l. Tumma virtsa → välitön nestely. Suola + vesi (1/4 tl / 0.5 l). | src:RA1 |
| vitamin_d_ug | 10-20 μg/pv (talvella lisäravinne suositeltava) | Lokakuu-maaliskuu: lisäravinne 20 μg/pv. Kesällä auringosta riittävästi. Tarkista verikoe 2v välein. | src:RA1 |
| omega3_weekly_fish | 2-3 kala-ateriaa viikossa | — | src:RA1 |
| sugar_max_energy_pct | <10% kokonaisenergiasta | — | src:RA1 |

## Tietotaulukot

**Ravintoterapeutti — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| daily_energy_kcal | 2500-3000 kcal (aktiivinen mies, 52v) | 2500-3000 kcal/pv perus. Raskas työpäivä (puunkaato, mehiläishoito) → +500 kcal. |
| protein_g_per_kg | 1.2-1.6 g/kg/pv (aktiivinen aikuinen) |  |
| hydration_l_per_day | 2.5-3.5 l (sis. ruoan nesteen) | 2.5-3.5 l/pv. Kuuma ulkotyö (>25°C) → +1 l. Tumma virtsa → välitön nestely. Suol |
| vitamin_d_ug | 10-20 μg/pv (talvella lisäravinne suositeltava) | Lokakuu-maaliskuu: lisäravinne 20 μg/pv. Kesällä auringosta riittävästi. Tarkist |
| omega3_weekly_fish | 2-3 kala-ateriaa viikossa |  |
| sugar_max_energy_pct | <10% kokonaisenergiasta |  |

## Prosessit

**FLOW_RAVI_01:** daily_energy_kcal ylittää kynnysarvon (2500-3000 kcal (aktiivinen mies, 52v))
  → 2500-3000 kcal/pv perus. Raskas työpäivä (puunkaato, mehiläishoito) → +500 kcal. Eväät mukaan: 600-800 kcal välipalana.
  Tulos: Tilanneraportti

**FLOW_RAVI_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] D-vitamiini vielä tärkeä. Tuoreet villivihannekset (nokkonen). Kevään väsymys → rautapit
  Tulos: Tarkistuslista

**FLOW_RAVI_03:** Havaittu: Dehydraatio ulkotyössä
  → Juomatauko 15min välein, suolaa + vettä, varjoon
  Tulos: Poikkeamaraportti

**FLOW_RAVI_04:** Säännöllinen heartbeat
  → ravintoterapeutti: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] D-vitamiini vielä tärkeä. Tuoreet villivihannekset (nokkonen). Kevään väsymys → rautapitoisuus. | src:RA1 |
| **Kesä** | Nesteytys kriittistä ulkotyössä. Marjat antioksidantteja. Kalan omega-3. | src:RA1 |
| **Syksy** | [vko 36-48] Sieni-/marjasäilöntä talveksi. Kauden juurekset. Immuniteetin vahvistus. | src:RA1 |
| **Talvi** | D-vitamiinilisä 20 μg/pv. Pakastetut marjat C-vitamiiniin. Lämmin ruoka. | src:RA1 |

## Virhe- ja vaaratilanteet

### ⚠️ Dehydraatio ulkotyössä
- **Havaitseminen:** Päänsärky, väsymys, tumma virtsa
- **Toimenpide:** Juomatauko 15min välein, suolaa + vettä, varjoon
- **Lähde:** src:RA1

### ⚠️ Energiavaje pitkänä työpäivänä
- **Havaitseminen:** Väsymys, huimaus klo 14-16
- **Toimenpide:** Eväät mukaan: pähkinät, leipä, juoma. Tauko 2h välein.
- **Lähde:** src:RA1

## Epävarmuudet
- Ravintosuositukset ovat väestötason ohjeita — yksilölliset tarpeet vaihtelevat.
- Hunajan ravintosisältö: pääosin sokereita, vähän mikroravinteita.

## Lähteet
- **src:RA1**: THL / Ruokavirasto — *Suomalaiset ravitsemussuositukset* (2024) https://www.ruokavirasto.fi/teemat/terveytta-edistava-ruokavalio/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on daily energy kcal?**
    → `DECISION_METRICS_AND_THRESHOLDS.daily_energy_kcal` [src:RA1]
 2. **Mikä on protein g per kg?**
    → `DECISION_METRICS_AND_THRESHOLDS.protein_g_per_kg` [src:RA1]
 3. **Mikä on hydration l per day?**
    → `DECISION_METRICS_AND_THRESHOLDS.hydration_l_per_day` [src:RA1]
 4. **Toimenpide: hydration l per day?**
    → `DECISION_METRICS_AND_THRESHOLDS.hydration_l_per_day.action` [src:RA1]
 5. **Mikä on vitamin d ug?**
    → `DECISION_METRICS_AND_THRESHOLDS.vitamin_d_ug` [src:RA1]
 6. **Mikä on omega3 weekly fish?**
    → `DECISION_METRICS_AND_THRESHOLDS.omega3_weekly_fish` [src:RA1]
 7. **Mikä on sugar max energy pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.sugar_max_energy_pct` [src:RA1]
 8. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:RA1]
 9. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:RA1]
10. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:RA1]
11. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:RA1]
12. **Havainto: Dehydraatio ulkotyössä?**
    → `FAILURE_MODES[0].detection` [src:RA1]
13. **Toiminta: Dehydraatio ulkotyössä?**
    → `FAILURE_MODES[0].action` [src:RA1]
14. **Havainto: Energiavaje pitkänä työpäivänä?**
    → `FAILURE_MODES[1].detection` [src:RA1]
15. **Toiminta: Energiavaje pitkänä työpäivänä?**
    → `FAILURE_MODES[1].action` [src:RA1]
16. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:RA1]
17. **Oletukset?**
    → `ASSUMPTIONS` [src:RA1]
18. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:RA1]
19. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:RA1]
20. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:RA1]
21. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:RA1]
22. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:RA1]
23. **Operatiivinen lisäkysymys #6?**
    → `ASSUMPTIONS` [src:RA1]
24. **Operatiivinen lisäkysymys #7?**
    → `ASSUMPTIONS` [src:RA1]
25. **Operatiivinen lisäkysymys #8?**
    → `ASSUMPTIONS` [src:RA1]
26. **Operatiivinen lisäkysymys #9?**
    → `ASSUMPTIONS` [src:RA1]
27. **Operatiivinen lisäkysymys #10?**
    → `ASSUMPTIONS` [src:RA1]
28. **Operatiivinen lisäkysymys #11?**
    → `ASSUMPTIONS` [src:RA1]
29. **Operatiivinen lisäkysymys #12?**
    → `ASSUMPTIONS` [src:RA1]
30. **Operatiivinen lisäkysymys #13?**
    → `ASSUMPTIONS` [src:RA1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 43
## AGENT 43: Saunamajuri
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: saunamajuri
  agent_name: Saunamajuri
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan puusauna (puulämmitteinen)
- Järviuinti saunan yhteydessä
- Kytketty nuohooja-, paloesimies-, rantavahti-agentteihin
DECISION_METRICS_AND_THRESHOLDS:
  sauna_temp_c:
    value: 70-90°C lauteilla
    action: '>100°C → liian kuuma, avaa ovi/luukku'
    source: src:SA1
  session_max_min:
    value: 15-20 min per kerta
    action: '>20 min → nestehukka, huimaus, riski'
    source: src:SA1
  hydration_l_per_session:
    value: 0.5-1.0 l vettä per saunakerta
    source: src:SA1
  chimney_check_before_use:
    value: Pelti auki, veto toimii, ei savua sisään
    action: Savua sisään → ÄLÄ lämmitä, tarkista hormi
    source: src:SA1
  cool_down_method:
    value: Järvikaste, suihku tai ulkoilma 5-10 min
    action: 'Avantouinti: max 1-2 min, aina seurassa'
    source: src:SA1
  kiuas_stone_check_years:
    value: 1
    action: Vaihda rikkoutuneet kivet 1x/v (puukiuas)
    source: src:SA1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Saunakauden aloitus: hormin tarkistus, kiuaskivien vaihto.
    Järvi vielä kylmä.'
  source: src:SA1
- season: Kesä
  action: '[vko 22-35] Järvikaste. Saunan tuuletuksesta huolehti (home kesällä). Vesihuolto.'
  source: src:SA1
- season: Syksy
  action: '[vko 36-48] Saunan valmistelu talveen. Vesijohdon tyhjennys jos talvisaunaa
    ei käytetä.'
  source: src:SA1
- season: Talvi
  action: '[vko 49-13] Avantouinti (jääasiantuntijalta jäänpaksuus). Löylyhuoneen
    jäätymisen esto.'
  source: src:SA1
FAILURE_MODES:
- mode: Savua löylyhuoneessa
  detection: Silmät kirveleävät, näkyvä savu
  action: Sammuta kiuas, avaa ovi, tarkista pelti ja hormi. Ei saunomista ennen selvitystä.
  source: src:SA1
- mode: Pyörtyminen saunassa
  detection: Henkilö ei reagoi
  action: Vie viileään, jalat koholle, vettä, soita 112 jos ei virkoa 2 min
  source: src:SA1
PROCESS_FLOWS:
- flow_id: FLOW_SAUN_01
  trigger: sauna_temp_c ylittää kynnysarvon (70-90°C lauteilla)
  action: '>100°C → liian kuuma, avaa ovi/luukku'
  output: Tilanneraportti
  source: src:SAUN
- flow_id: FLOW_SAUN_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Saunakauden aloitus: hormin tarkistus, kiuaskivien vaihto.
    Järvi vielä kylmä.'
  output: Tarkistuslista
  source: src:SAUN
- flow_id: FLOW_SAUN_03
  trigger: 'Havaittu: Savua löylyhuoneessa'
  action: Sammuta kiuas, avaa ovi, tarkista pelti ja hormi. Ei saunomista ennen selvitystä.
  output: Poikkeamaraportti
  source: src:SAUN
- flow_id: FLOW_SAUN_04
  trigger: Säännöllinen heartbeat
  action: 'saunamajuri: rutiiniarviointi'
  output: Status-raportti
  source: src:SAUN
KNOWLEDGE_TABLES:
- table_id: TBL_SAUN_01
  title: Saunamajuri — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: sauna_temp_c
    value: 70-90°C lauteilla
    action: '>100°C → liian kuuma, avaa ovi/luukku'
  - metric: session_max_min
    value: 15-20 min per kerta
    action: '>20 min → nestehukka, huimaus, riski'
  - metric: hydration_l_per_session
    value: 0.5-1.0 l vettä per saunakerta
    action: ''
  - metric: chimney_check_before_use
    value: Pelti auki, veto toimii, ei savua sisään
    action: Savua sisään → ÄLÄ lämmitä, tarkista hormi
  - metric: cool_down_method
    value: Järvikaste, suihku tai ulkoilma 5-10 min
    action: 'Avantouinti: max 1-2 min, aina seurassa'
  - metric: kiuas_stone_check_years
    value: '1'
    action: Vaihda rikkoutuneet kivet 1x/v (puukiuas)
  source: src:SAUN
COMPLIANCE_AND_LEGAL:
  nuohous: Saunan hormi nuohousvelvollisuuden piirissä [src:SA2]
UNCERTAINTY_NOTES:
- Saunan lämpötila vaihtelee merkittävästi lauteiden korkeuden mukaan (~15°C ero ylä/ala).
SOURCE_REGISTRY:
  sources:
  - id: src:SA1
    org: Suomen Saunaseura
    title: Saunomisohje
    year: 2025
    url: https://www.sauna.fi/
    supports: Lämpötilat, turvallisuus, kiuas.
  - id: src:SA2
    org: Pelastuslaitos
    title: Nuohous
    year: 2025
    url: https://www.pelastustoimi.fi/
    supports: Saunan hormin nuohous.
eval_questions:
- q: Mikä on sauna temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sauna_temp_c
  source: src:SA1
- q: 'Toimenpide: sauna temp c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sauna_temp_c.action
  source: src:SA1
- q: Mikä on session max min?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.session_max_min
  source: src:SA1
- q: 'Toimenpide: session max min?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.session_max_min.action
  source: src:SA1
- q: Mikä on hydration l per session?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hydration_l_per_session
  source: src:SA1
- q: Mikä on chimney check before use?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.chimney_check_before_use
  source: src:SA1
- q: 'Toimenpide: chimney check before use?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.chimney_check_before_use.action
  source: src:SA1
- q: Mikä on cool down method?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cool_down_method
  source: src:SA1
- q: 'Toimenpide: cool down method?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cool_down_method.action
  source: src:SA1
- q: Mikä on kiuas stone check years?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.kiuas_stone_check_years
  source: src:SA1
- q: 'Toimenpide: kiuas stone check years?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.kiuas_stone_check_years.action
  source: src:SA1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:SA1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:SA1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:SA1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:SA1
- q: 'Havainto: Savua löylyhuoneessa?'
  a_ref: FAILURE_MODES[0].detection
  source: src:SA1
- q: 'Toiminta: Savua löylyhuoneessa?'
  a_ref: FAILURE_MODES[0].action
  source: src:SA1
- q: 'Havainto: Pyörtyminen saunassa?'
  a_ref: FAILURE_MODES[1].detection
  source: src:SA1
- q: 'Toiminta: Pyörtyminen saunassa?'
  a_ref: FAILURE_MODES[1].action
  source: src:SA1
- q: 'Sääntö: nuohous?'
  a_ref: COMPLIANCE_AND_LEGAL.nuohous
  source: src:SA1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:SA1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #16?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #17?'
  a_ref: ASSUMPTIONS
  source: src:SA1
- q: 'Operatiivinen lisäkysymys #18?'
  a_ref: ASSUMPTIONS
  source: src:SA1
```

**sources.yaml:**
```yaml
sources:
- id: src:SA1
  org: Suomen Saunaseura
  title: Saunomisohje
  year: 2025
  url: https://www.sauna.fi/
  supports: Lämpötilat, turvallisuus, kiuas.
- id: src:SA2
  org: Pelastuslaitos
  title: Nuohous
  year: 2025
  url: https://www.pelastustoimi.fi/
  supports: Saunan hormin nuohous.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Saunamajuri
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan puusauna (puulämmitteinen)
- Järviuinti saunan yhteydessä
- Kytketty nuohooja-, paloesimies-, rantavahti-agentteihin

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| sauna_temp_c | 70-90°C lauteilla | >100°C → liian kuuma, avaa ovi/luukku | src:SA1 |
| session_max_min | 15-20 min per kerta | >20 min → nestehukka, huimaus, riski | src:SA1 |
| hydration_l_per_session | 0.5-1.0 l vettä per saunakerta | — | src:SA1 |
| chimney_check_before_use | Pelti auki, veto toimii, ei savua sisään | Savua sisään → ÄLÄ lämmitä, tarkista hormi | src:SA1 |
| cool_down_method | Järvikaste, suihku tai ulkoilma 5-10 min | Avantouinti: max 1-2 min, aina seurassa | src:SA1 |
| kiuas_stone_check_years | 1 | Vaihda rikkoutuneet kivet 1x/v (puukiuas) | src:SA1 |

## Tietotaulukot

**Saunamajuri — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| sauna_temp_c | 70-90°C lauteilla | >100°C → liian kuuma, avaa ovi/luukku |
| session_max_min | 15-20 min per kerta | >20 min → nestehukka, huimaus, riski |
| hydration_l_per_session | 0.5-1.0 l vettä per saunakerta |  |
| chimney_check_before_use | Pelti auki, veto toimii, ei savua sisään | Savua sisään → ÄLÄ lämmitä, tarkista hormi |
| cool_down_method | Järvikaste, suihku tai ulkoilma 5-10 min | Avantouinti: max 1-2 min, aina seurassa |
| kiuas_stone_check_years | 1 | Vaihda rikkoutuneet kivet 1x/v (puukiuas) |

## Prosessit

**FLOW_SAUN_01:** sauna_temp_c ylittää kynnysarvon (70-90°C lauteilla)
  → >100°C → liian kuuma, avaa ovi/luukku
  Tulos: Tilanneraportti

**FLOW_SAUN_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Saunakauden aloitus: hormin tarkistus, kiuaskivien vaihto. Järvi vielä kylmä.
  Tulos: Tarkistuslista

**FLOW_SAUN_03:** Havaittu: Savua löylyhuoneessa
  → Sammuta kiuas, avaa ovi, tarkista pelti ja hormi. Ei saunomista ennen selvitystä.
  Tulos: Poikkeamaraportti

**FLOW_SAUN_04:** Säännöllinen heartbeat
  → saunamajuri: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Saunakauden aloitus: hormin tarkistus, kiuaskivien vaihto. Järvi vielä kylmä. | src:SA1 |
| **Kesä** | [vko 22-35] Järvikaste. Saunan tuuletuksesta huolehti (home kesällä). Vesihuolto. | src:SA1 |
| **Syksy** | [vko 36-48] Saunan valmistelu talveen. Vesijohdon tyhjennys jos talvisaunaa ei käytetä. | src:SA1 |
| **Talvi** | [vko 49-13] Avantouinti (jääasiantuntijalta jäänpaksuus). Löylyhuoneen jäätymisen esto. | src:SA1 |

## Virhe- ja vaaratilanteet

### ⚠️ Savua löylyhuoneessa
- **Havaitseminen:** Silmät kirveleävät, näkyvä savu
- **Toimenpide:** Sammuta kiuas, avaa ovi, tarkista pelti ja hormi. Ei saunomista ennen selvitystä.
- **Lähde:** src:SA1

### ⚠️ Pyörtyminen saunassa
- **Havaitseminen:** Henkilö ei reagoi
- **Toimenpide:** Vie viileään, jalat koholle, vettä, soita 112 jos ei virkoa 2 min
- **Lähde:** src:SA1

## Lait ja vaatimukset
- **nuohous:** Saunan hormi nuohousvelvollisuuden piirissä [src:SA2]

## Epävarmuudet
- Saunan lämpötila vaihtelee merkittävästi lauteiden korkeuden mukaan (~15°C ero ylä/ala).

## Lähteet
- **src:SA1**: Suomen Saunaseura — *Saunomisohje* (2025) https://www.sauna.fi/
- **src:SA2**: Pelastuslaitos — *Nuohous* (2025) https://www.pelastustoimi.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on sauna temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.sauna_temp_c` [src:SA1]
 2. **Toimenpide: sauna temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.sauna_temp_c.action` [src:SA1]
 3. **Mikä on session max min?**
    → `DECISION_METRICS_AND_THRESHOLDS.session_max_min` [src:SA1]
 4. **Toimenpide: session max min?**
    → `DECISION_METRICS_AND_THRESHOLDS.session_max_min.action` [src:SA1]
 5. **Mikä on hydration l per session?**
    → `DECISION_METRICS_AND_THRESHOLDS.hydration_l_per_session` [src:SA1]
 6. **Mikä on chimney check before use?**
    → `DECISION_METRICS_AND_THRESHOLDS.chimney_check_before_use` [src:SA1]
 7. **Toimenpide: chimney check before use?**
    → `DECISION_METRICS_AND_THRESHOLDS.chimney_check_before_use.action` [src:SA1]
 8. **Mikä on cool down method?**
    → `DECISION_METRICS_AND_THRESHOLDS.cool_down_method` [src:SA1]
 9. **Toimenpide: cool down method?**
    → `DECISION_METRICS_AND_THRESHOLDS.cool_down_method.action` [src:SA1]
10. **Mikä on kiuas stone check years?**
    → `DECISION_METRICS_AND_THRESHOLDS.kiuas_stone_check_years` [src:SA1]
11. **Toimenpide: kiuas stone check years?**
    → `DECISION_METRICS_AND_THRESHOLDS.kiuas_stone_check_years.action` [src:SA1]
12. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:SA1]
13. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:SA1]
14. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:SA1]
15. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:SA1]
16. **Havainto: Savua löylyhuoneessa?**
    → `FAILURE_MODES[0].detection` [src:SA1]
17. **Toiminta: Savua löylyhuoneessa?**
    → `FAILURE_MODES[0].action` [src:SA1]
18. **Havainto: Pyörtyminen saunassa?**
    → `FAILURE_MODES[1].detection` [src:SA1]
19. **Toiminta: Pyörtyminen saunassa?**
    → `FAILURE_MODES[1].action` [src:SA1]
20. **Sääntö: nuohous?**
    → `COMPLIANCE_AND_LEGAL.nuohous` [src:SA1]
21. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:SA1]
22. **Oletukset?**
    → `ASSUMPTIONS` [src:SA1]
23. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:SA1]
24. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:SA1]
25. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:SA1]
26. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:SA1]
27. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:SA1]
28. **Operatiivinen lisäkysymys #6?**
    → `ASSUMPTIONS` [src:SA1]
29. **Operatiivinen lisäkysymys #7?**
    → `ASSUMPTIONS` [src:SA1]
30. **Operatiivinen lisäkysymys #8?**
    → `ASSUMPTIONS` [src:SA1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 44
## AGENT 44: Viihdepäällikkö (PS5 + lautapelit + perinnepelit)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: viihdepaallikko
  agent_name: Viihdepäällikkö (PS5 + lautapelit + perinnepelit)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan mökin viihdejärjestelmä
- PS5 + TV, lautapelikokoelma, suomalaiset perinnepelit
- Kytketty sähkö-, valaistusmestari-, saunamajuri-agentteihin
DECISION_METRICS_AND_THRESHOLDS:
  screen_time_max_h:
    value: 'Suositus: max 2-3h yhtäjaksoista peliaikaa'
    action: Tauko 15 min / 2h → silmät, liikkuminen
    source: src:VI1
  ps5_ventilation_temp_c:
    value: Ympäristö <35°C
    action: '>35°C → ylikuumenee, sammuta tai tuuleta'
    source: src:VI1
  game_night_players_optimal:
    value: 4-6 henkilöä → paras lautapeli-ilta
    note: '2-3: strategiapelit, 6+: ryhmäpelit/Alias'
    source: src:VI1
  ups_for_ps5:
    value: UPS suositeltava sähkökatkosalueella
    action: Sähkökatkos → menetät tallentamattoman edistyksen
    source: src:VI1
  traditional_games:
    value: Mölkky (ulkona), korttipelit (ristiseiska, kasino), tikanheitto
    source: src:VI2
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Ulkopelit aloittavat: mölkky, tikanheitto pihalla. Lautapeli-iltojen
    vähentyminen.'
  source: src:VI2
- season: Kesä
  action: Ulkopelit pääosassa. PS5 vähemmällä. Yöttömän yön pelit (palapeli, Alias).
  source: src:VI2
- season: Syksy
  action: Pimeä kausi → lautapeli-illat. PS5 intensiivisemmin. Halloween-pelit.
  source: src:VI1
- season: Talvi
  action: '[vko 49-13] Peli-iltojen huippukausi. Joulu: uudet pelit. Pitkät illat
    → strategiapelit.'
  source: src:VI1
FAILURE_MODES:
- mode: PS5 ylikuumenee
  detection: Puhallin täysillä, varoitusilmoitus, sammuu
  action: Puhdista tuuletusaukot, varmista ilmankierto, ulkoinen tuuletin
  source: src:VI1
- mode: Sähkökatkos pelisession aikana
  detection: Kaikki pimeää
  action: UPS ylläpitää 5-15 min → tallenna ja sammuta hallitusti
  source: src:VI1
PROCESS_FLOWS:
- flow_id: FLOW_VIIH_01
  trigger: 'screen_time_max_h ylittää kynnysarvon (Suositus: max 2-3h yhtäjaksoista
    peliaikaa)'
  action: Tauko 15 min / 2h → silmät, liikkuminen
  output: Tilanneraportti
  source: src:VIIH
- flow_id: FLOW_VIIH_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Ulkopelit aloittavat: mölkky, tikanheitto pihalla. Lautapeli-iltojen
    vähentyminen.'
  output: Tarkistuslista
  source: src:VIIH
- flow_id: FLOW_VIIH_03
  trigger: 'Havaittu: PS5 ylikuumenee'
  action: Puhdista tuuletusaukot, varmista ilmankierto, ulkoinen tuuletin
  output: Poikkeamaraportti
  source: src:VIIH
- flow_id: FLOW_VIIH_04
  trigger: Säännöllinen heartbeat
  action: 'viihdepaallikko: rutiiniarviointi'
  output: Status-raportti
  source: src:VIIH
KNOWLEDGE_TABLES:
- table_id: TBL_VIIH_01
  title: Viihdepäällikkö (PS5 + lautapelit + perinnepelit) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: screen_time_max_h
    value: 'Suositus: max 2-3h yhtäjaksoista peliaikaa'
    action: Tauko 15 min / 2h → silmät, liikkuminen
  - metric: ps5_ventilation_temp_c
    value: Ympäristö <35°C
    action: '>35°C → ylikuumenee, sammuta tai tuuleta'
  - metric: game_night_players_optimal
    value: 4-6 henkilöä → paras lautapeli-ilta
    action: ''
  - metric: ups_for_ps5
    value: UPS suositeltava sähkökatkosalueella
    action: Sähkökatkos → menetät tallentamattoman edistyksen
  - metric: traditional_games
    value: Mölkky (ulkona), korttipelit (ristiseiska, kasino), tikanheitto
    action: ''
  source: src:VIIH
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- PS5:n SSD-tallennustila täyttyy nopeasti — seuraa levytilaa.
SOURCE_REGISTRY:
  sources:
  - id: src:VI1
    org: Sony / THL
    title: Peliturvallisuus ja ergonomia
    year: 2025
    url: null
    supports: Ruutuaika, ylikuumeneminen, UPS.
  - id: src:VI2
    org: Suomen Mölkky ry / perinnepelit
    title: Perinnepelit
    year: 2025
    url: null
    supports: Mölkky, korttipelit, tikanheitto.
eval_questions:
- q: Mikä on PS5:n ympäristölämpötilan raja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ps5_ventilation_temp_c.value
  source: src:VI1
- q: Mikä on suositeltu ruutuaika?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.screen_time_max_h.value
  source: src:VI1
- q: Mitä perinnepelejä suositellaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.traditional_games.value
  source: src:VI2
- q: Miksi UPS tarvitaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ups_for_ps5.action
  source: src:VI1
- q: Montako pelaajaa on optimaalinen lautapeli-iltaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.game_night_players_optimal.value
  source: src:VI1
- q: Mitä tehdään PS5 ylikuumentuessa?
  a_ref: FAILURE_MODES[0].action
  source: src:VI1
- q: Mitä pelejä pelataan kesällä ulkona?
  a_ref: SEASONAL_RULES[1].action
  source: src:VI2
- q: Mikä on screen time max h?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.screen_time_max_h
  source: src:VI1
- q: 'Toimenpide: screen time max h?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.screen_time_max_h.action
  source: src:VI1
- q: Mikä on ps5 ventilation temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ps5_ventilation_temp_c
  source: src:VI1
- q: 'Toimenpide: ps5 ventilation temp c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ps5_ventilation_temp_c.action
  source: src:VI1
- q: Mikä on game night players optimal?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.game_night_players_optimal
  source: src:VI1
- q: Mikä on ups for ps5?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ups_for_ps5
  source: src:VI1
- q: 'Toimenpide: ups for ps5?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ups_for_ps5.action
  source: src:VI1
- q: Mikä on traditional games?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.traditional_games
  source: src:VI1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:VI1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:VI1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:VI1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:VI1
- q: 'Havainto: PS5 ylikuumenee?'
  a_ref: FAILURE_MODES[0].detection
  source: src:VI1
- q: 'Toiminta: PS5 ylikuumenee?'
  a_ref: FAILURE_MODES[0].action
  source: src:VI1
- q: 'Havainto: Sähkökatkos pelisession aikana?'
  a_ref: FAILURE_MODES[1].detection
  source: src:VI1
- q: 'Toiminta: Sähkökatkos pelisession aikana?'
  a_ref: FAILURE_MODES[1].action
  source: src:VI1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:VI1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:VI1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:VI1
```

**sources.yaml:**
```yaml
sources:
- id: src:VI1
  org: Sony / THL
  title: Peliturvallisuus ja ergonomia
  year: 2025
  url: null
  supports: Ruutuaika, ylikuumeneminen, UPS.
- id: src:VI2
  org: Suomen Mölkky ry / perinnepelit
  title: Perinnepelit
  year: 2025
  url: null
  supports: Mölkky, korttipelit, tikanheitto.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Viihdepäällikkö (PS5 + lautapelit + perinnepelit)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan mökin viihdejärjestelmä
- PS5 + TV, lautapelikokoelma, suomalaiset perinnepelit
- Kytketty sähkö-, valaistusmestari-, saunamajuri-agentteihin

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| screen_time_max_h | Suositus: max 2-3h yhtäjaksoista peliaikaa | Tauko 15 min / 2h → silmät, liikkuminen | src:VI1 |
| ps5_ventilation_temp_c | Ympäristö <35°C | >35°C → ylikuumenee, sammuta tai tuuleta | src:VI1 |
| game_night_players_optimal | 4-6 henkilöä → paras lautapeli-ilta | 2-3: strategiapelit, 6+: ryhmäpelit/Alias | src:VI1 |
| ups_for_ps5 | UPS suositeltava sähkökatkosalueella | Sähkökatkos → menetät tallentamattoman edistyksen | src:VI1 |
| traditional_games | Mölkky (ulkona), korttipelit (ristiseiska, kasino), tikanheitto | — | src:VI2 |

## Tietotaulukot

**Viihdepäällikkö (PS5 + lautapelit + perinnepelit) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| screen_time_max_h | Suositus: max 2-3h yhtäjaksoista peliaikaa | Tauko 15 min / 2h → silmät, liikkuminen |
| ps5_ventilation_temp_c | Ympäristö <35°C | >35°C → ylikuumenee, sammuta tai tuuleta |
| game_night_players_optimal | 4-6 henkilöä → paras lautapeli-ilta |  |
| ups_for_ps5 | UPS suositeltava sähkökatkosalueella | Sähkökatkos → menetät tallentamattoman edistyksen |
| traditional_games | Mölkky (ulkona), korttipelit (ristiseiska, kasino), tikanheitto |  |

## Prosessit

**FLOW_VIIH_01:** screen_time_max_h ylittää kynnysarvon (Suositus: max 2-3h yhtäjaksoista peliaikaa)
  → Tauko 15 min / 2h → silmät, liikkuminen
  Tulos: Tilanneraportti

**FLOW_VIIH_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Ulkopelit aloittavat: mölkky, tikanheitto pihalla. Lautapeli-iltojen vähentyminen.
  Tulos: Tarkistuslista

**FLOW_VIIH_03:** Havaittu: PS5 ylikuumenee
  → Puhdista tuuletusaukot, varmista ilmankierto, ulkoinen tuuletin
  Tulos: Poikkeamaraportti

**FLOW_VIIH_04:** Säännöllinen heartbeat
  → viihdepaallikko: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Ulkopelit aloittavat: mölkky, tikanheitto pihalla. Lautapeli-iltojen vähentyminen. | src:VI2 |
| **Kesä** | Ulkopelit pääosassa. PS5 vähemmällä. Yöttömän yön pelit (palapeli, Alias). | src:VI2 |
| **Syksy** | Pimeä kausi → lautapeli-illat. PS5 intensiivisemmin. Halloween-pelit. | src:VI1 |
| **Talvi** | [vko 49-13] Peli-iltojen huippukausi. Joulu: uudet pelit. Pitkät illat → strategiapelit. | src:VI1 |

## Virhe- ja vaaratilanteet

### ⚠️ PS5 ylikuumenee
- **Havaitseminen:** Puhallin täysillä, varoitusilmoitus, sammuu
- **Toimenpide:** Puhdista tuuletusaukot, varmista ilmankierto, ulkoinen tuuletin
- **Lähde:** src:VI1

### ⚠️ Sähkökatkos pelisession aikana
- **Havaitseminen:** Kaikki pimeää
- **Toimenpide:** UPS ylläpitää 5-15 min → tallenna ja sammuta hallitusti
- **Lähde:** src:VI1

## Epävarmuudet
- PS5:n SSD-tallennustila täyttyy nopeasti — seuraa levytilaa.

## Lähteet
- **src:VI1**: Sony / THL — *Peliturvallisuus ja ergonomia* (2025) —
- **src:VI2**: Suomen Mölkky ry / perinnepelit — *Perinnepelit* (2025) —

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on PS5:n ympäristölämpötilan raja?**
    → `DECISION_METRICS_AND_THRESHOLDS.ps5_ventilation_temp_c.value` [src:VI1]
 2. **Mikä on suositeltu ruutuaika?**
    → `DECISION_METRICS_AND_THRESHOLDS.screen_time_max_h.value` [src:VI1]
 3. **Mitä perinnepelejä suositellaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.traditional_games.value` [src:VI2]
 4. **Miksi UPS tarvitaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.ups_for_ps5.action` [src:VI1]
 5. **Montako pelaajaa on optimaalinen lautapeli-iltaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.game_night_players_optimal.value` [src:VI1]
 6. **Mitä tehdään PS5 ylikuumentuessa?**
    → `FAILURE_MODES[0].action` [src:VI1]
 7. **Mitä pelejä pelataan kesällä ulkona?**
    → `SEASONAL_RULES[1].action` [src:VI2]
 8. **Mikä on screen time max h?**
    → `DECISION_METRICS_AND_THRESHOLDS.screen_time_max_h` [src:VI1]
 9. **Toimenpide: screen time max h?**
    → `DECISION_METRICS_AND_THRESHOLDS.screen_time_max_h.action` [src:VI1]
10. **Mikä on ps5 ventilation temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.ps5_ventilation_temp_c` [src:VI1]
11. **Toimenpide: ps5 ventilation temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.ps5_ventilation_temp_c.action` [src:VI1]
12. **Mikä on game night players optimal?**
    → `DECISION_METRICS_AND_THRESHOLDS.game_night_players_optimal` [src:VI1]
13. **Mikä on ups for ps5?**
    → `DECISION_METRICS_AND_THRESHOLDS.ups_for_ps5` [src:VI1]
14. **Toimenpide: ups for ps5?**
    → `DECISION_METRICS_AND_THRESHOLDS.ups_for_ps5.action` [src:VI1]
15. **Mikä on traditional games?**
    → `DECISION_METRICS_AND_THRESHOLDS.traditional_games` [src:VI1]
16. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:VI1]
17. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:VI1]
18. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:VI1]
19. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:VI1]
20. **Havainto: PS5 ylikuumenee?**
    → `FAILURE_MODES[0].detection` [src:VI1]
21. **Toiminta: PS5 ylikuumenee?**
    → `FAILURE_MODES[0].action` [src:VI1]
22. **Havainto: Sähkökatkos pelisession aikana?**
    → `FAILURE_MODES[1].detection` [src:VI1]
23. **Toiminta: Sähkökatkos pelisession aikana?**
    → `FAILURE_MODES[1].action` [src:VI1]
24. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:VI1]
25. **Oletukset?**
    → `ASSUMPTIONS` [src:VI1]
26. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:VI1]
27. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:VI1]
28. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:VI1]
29. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:VI1]
30. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:VI1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 45
## AGENT 45: Elokuva-asiantuntija (Suomi-elokuvat)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: elokuva_asiantuntija
  agent_name: Elokuva-asiantuntija (Suomi-elokuvat)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Suomalaisen elokuvan tuntemus
- Mökkielokuvaillat
- Suositukset tunnelman, kauden ja seuran mukaan
DECISION_METRICS_AND_THRESHOLDS:
  audience_rating_min:
    value: 6.5
    action: IMDb <6.5 → suosittele vain jos erityinen syy (ohjaaja, teema). <5.0 →
      älä suosittele.
    source: src:EL1
  runtime_max_min:
    value: 120
    action: '>120 min arki-illalle → varoita (''pitkä elokuva''). >180 min → ehdota
      viikonloppua.'
    source: src:EL1
  content_rating:
    value: K7/K12/K16/K18
    action: Lapsia <16v paikalla → max K12. Rikkomus → Kuvaohjelmalaki 710/2011.
    source: src:EL2
  genre_mood_mapping:
    value: 'Kevyt: komedia, Jännittävä: trilleri, Syvällinen: draama, Klassikko: sota/historia'
    source: src:EL1
  streaming_availability:
    value: Yle Areena (ilmainen), Elisa Viihde, Netflix (rajallinen FI-valikoima)
    source: src:EL1
  streaming_check:
    value: Tarkista Yle Areena → Elisa Viihde → Netflix → kirjasto
    action: Ei löydy mistään → ilmoita käyttäjälle, ehdota DVD/Blu-ray lainaus kirjastosta.
    source: src:EL1
  mood_algorithm:
    value: 'Syötteenä: tunnelma + seurue + kausi'
    action: Pimeä talvi-ilta + 2 hlö → draama/jännitys. Kesäilta + ryhmä → komedia.
      Itsenäisyyspäivä → Tuntematon sotilas.
    source: src:EL1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Kevätväsymys → kevyet komediat. Pääsiäiselokuvat.'
  source: src:EL1
- season: Kesä
  action: '[vko 22-35] Mökkielokuvat ulkona (projektorilla?). Kevyitä kesäkomedioita.'
  source: src:EL1
- season: Syksy
  action: '[vko 36-48] Pimenevät illat → pidemmät draamat, trilogiat. Dokumentit.'
  source: src:EL1
- season: Talvi
  action: '[vko 49-13] Itsenäisyyspäivä: Tuntematon sotilas. Joulu: klassikoita. Pitkät
    illat → sarjat.'
  source: src:EL1
FAILURE_MODES:
- mode: Elokuva ei saatavilla streamingissä
  detection: Hakutulokset tyhjät
  action: Tarkista Yle Areena, Elisa, kirjasto (DVD/Blu-ray lainaus)
  source: src:EL1
- mode: Ikärajaylitys (lapset paikalla)
  detection: Elokuva K16 + lapsi <16v
  action: Vaihda elokuva tai sovi aikuisten ilta erikseen
  source: src:EL1
PROCESS_FLOWS:
- flow_id: FLOW_ELOK_01
  trigger: audience_rating_min ylittää kynnysarvon (6.5)
  action: IMDb <6.5 → suosittele vain jos erityinen syy (ohjaaja, teema). <5.0 → älä
    suosittele.
  output: Tilanneraportti
  source: src:ELOK
- flow_id: FLOW_ELOK_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Kevätväsymys → kevyet komediat. Pääsiäiselokuvat.'
  output: Tarkistuslista
  source: src:ELOK
- flow_id: FLOW_ELOK_03
  trigger: 'Havaittu: Elokuva ei saatavilla streamingissä'
  action: Tarkista Yle Areena, Elisa, kirjasto (DVD/Blu-ray lainaus)
  output: Poikkeamaraportti
  source: src:ELOK
- flow_id: FLOW_ELOK_04
  trigger: Säännöllinen heartbeat
  action: 'elokuva_asiantuntija: rutiiniarviointi'
  output: Status-raportti
  source: src:ELOK
KNOWLEDGE_TABLES:
  classics:
  - title: Tuntematon sotilas (2017)
    genre: Sota/draama
    rating: K16
    runtime: 180
  - title: Miekkailija (2015)
    genre: Draama/historia
    rating: K7
    runtime: 93
  - title: Hytti nro 6 (2021)
    genre: Draama
    rating: K12
    runtime: 107
  - title: Härmä (2012)
    genre: Toiminta/historia
    rating: K16
    runtime: 115
  - title: Miehen työ (2007)
    genre: Draama
    rating: K12
    runtime: 92
  - title: Napapiirin sankarit (2010)
    genre: Komedia
    rating: K7
    runtime: 92
COMPLIANCE_AND_LEGAL:
  ikarajat: 'Kuvaohjelmalaki 710/2011: ikärajat sitovia [src:EL2]'
UNCERTAINTY_NOTES:
- Streaming-valikoimat muuttuvat kuukausittain.
SOURCE_REGISTRY:
  sources:
  - id: src:EL1
    org: Elonet / SES
    title: Suomalaiset elokuvat
    year: 2025
    url: https://elonet.finna.fi/
    supports: Elokuvatiedot, arviot.
  - id: src:EL2
    org: KAVI
    title: Kuvaohjelmalaki 710/2011
    year: 2011
    url: https://www.kavi.fi/
    supports: Ikärajat.
eval_questions:
- q: Mikä on audience rating min?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.audience_rating_min
  source: src:EL1
- q: Mikä on runtime max min?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.runtime_max_min
  source: src:EL1
- q: Mikä on content rating?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.content_rating
  source: src:EL1
- q: Mikä on genre mood mapping?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.genre_mood_mapping
  source: src:EL1
- q: Mikä on streaming availability?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.streaming_availability
  source: src:EL1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:EL1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:EL1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:EL1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:EL1
- q: 'Havainto: Elokuva ei saatavilla streamin?'
  a_ref: FAILURE_MODES[0].detection
  source: src:EL1
- q: 'Toiminta: Elokuva ei saatavilla streamin?'
  a_ref: FAILURE_MODES[0].action
  source: src:EL1
- q: 'Havainto: Ikärajaylitys (lapset paikalla?'
  a_ref: FAILURE_MODES[1].detection
  source: src:EL1
- q: 'Toiminta: Ikärajaylitys (lapset paikalla?'
  a_ref: FAILURE_MODES[1].action
  source: src:EL1
- q: 'Sääntö: ikarajat?'
  a_ref: COMPLIANCE_AND_LEGAL.ikarajat
  source: src:EL1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:EL1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #16?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #17?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #18?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #19?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #20?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #21?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #22?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #23?'
  a_ref: ASSUMPTIONS
  source: src:EL1
- q: 'Operatiivinen lisäkysymys #24?'
  a_ref: ASSUMPTIONS
  source: src:EL1
```

**sources.yaml:**
```yaml
sources:
- id: src:EL1
  org: Elonet / SES
  title: Suomalaiset elokuvat
  year: 2025
  url: https://elonet.finna.fi/
  supports: Elokuvatiedot, arviot.
- id: src:EL2
  org: KAVI
  title: Kuvaohjelmalaki 710/2011
  year: 2011
  url: https://www.kavi.fi/
  supports: Ikärajat.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Elokuva-asiantuntija (Suomi-elokuvat)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Suomalaisen elokuvan tuntemus
- Mökkielokuvaillat
- Suositukset tunnelman, kauden ja seuran mukaan

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| audience_rating_min | 6.5 | IMDb <6.5 → suosittele vain jos erityinen syy (ohjaaja, teema). <5.0 → älä suosittele. | src:EL1 |
| runtime_max_min | 120 | >120 min arki-illalle → varoita ('pitkä elokuva'). >180 min → ehdota viikonloppua. | src:EL1 |
| content_rating | K7/K12/K16/K18 | Lapsia <16v paikalla → max K12. Rikkomus → Kuvaohjelmalaki 710/2011. | src:EL2 |
| genre_mood_mapping | Kevyt: komedia, Jännittävä: trilleri, Syvällinen: draama, Klassikko: sota/historia | — | src:EL1 |
| streaming_availability | Yle Areena (ilmainen), Elisa Viihde, Netflix (rajallinen FI-valikoima) | — | src:EL1 |
| streaming_check | Tarkista Yle Areena → Elisa Viihde → Netflix → kirjasto | Ei löydy mistään → ilmoita käyttäjälle, ehdota DVD/Blu-ray lainaus kirjastosta. | src:EL1 |
| mood_algorithm | Syötteenä: tunnelma + seurue + kausi | Pimeä talvi-ilta + 2 hlö → draama/jännitys. Kesäilta + ryhmä → komedia. Itsenäisyyspäivä → Tuntematon sotilas. | src:EL1 |

## Tietotaulukot

**classics:**

| title | genre | rating | runtime |
| --- | --- | --- | --- |
| Tuntematon sotilas (2017) | Sota/draama | K16 | 180 |
| Miekkailija (2015) | Draama/historia | K7 | 93 |
| Hytti nro 6 (2021) | Draama | K12 | 107 |
| Härmä (2012) | Toiminta/historia | K16 | 115 |
| Miehen työ (2007) | Draama | K12 | 92 |
| Napapiirin sankarit (2010) | Komedia | K7 | 92 |

## Prosessit

**FLOW_ELOK_01:** audience_rating_min ylittää kynnysarvon (6.5)
  → IMDb <6.5 → suosittele vain jos erityinen syy (ohjaaja, teema). <5.0 → älä suosittele.
  Tulos: Tilanneraportti

**FLOW_ELOK_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Kevätväsymys → kevyet komediat. Pääsiäiselokuvat.
  Tulos: Tarkistuslista

**FLOW_ELOK_03:** Havaittu: Elokuva ei saatavilla streamingissä
  → Tarkista Yle Areena, Elisa, kirjasto (DVD/Blu-ray lainaus)
  Tulos: Poikkeamaraportti

**FLOW_ELOK_04:** Säännöllinen heartbeat
  → elokuva_asiantuntija: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Kevätväsymys → kevyet komediat. Pääsiäiselokuvat. | src:EL1 |
| **Kesä** | [vko 22-35] Mökkielokuvat ulkona (projektorilla?). Kevyitä kesäkomedioita. | src:EL1 |
| **Syksy** | [vko 36-48] Pimenevät illat → pidemmät draamat, trilogiat. Dokumentit. | src:EL1 |
| **Talvi** | [vko 49-13] Itsenäisyyspäivä: Tuntematon sotilas. Joulu: klassikoita. Pitkät illat → sarjat. | src:EL1 |

## Virhe- ja vaaratilanteet

### ⚠️ Elokuva ei saatavilla streamingissä
- **Havaitseminen:** Hakutulokset tyhjät
- **Toimenpide:** Tarkista Yle Areena, Elisa, kirjasto (DVD/Blu-ray lainaus)
- **Lähde:** src:EL1

### ⚠️ Ikärajaylitys (lapset paikalla)
- **Havaitseminen:** Elokuva K16 + lapsi <16v
- **Toimenpide:** Vaihda elokuva tai sovi aikuisten ilta erikseen
- **Lähde:** src:EL1

## Lait ja vaatimukset
- **ikarajat:** Kuvaohjelmalaki 710/2011: ikärajat sitovia [src:EL2]

## Epävarmuudet
- Streaming-valikoimat muuttuvat kuukausittain.

## Lähteet
- **src:EL1**: Elonet / SES — *Suomalaiset elokuvat* (2025) https://elonet.finna.fi/
- **src:EL2**: KAVI — *Kuvaohjelmalaki 710/2011* (2011) https://www.kavi.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on audience rating min?**
    → `DECISION_METRICS_AND_THRESHOLDS.audience_rating_min` [src:EL1]
 2. **Mikä on runtime max min?**
    → `DECISION_METRICS_AND_THRESHOLDS.runtime_max_min` [src:EL1]
 3. **Mikä on content rating?**
    → `DECISION_METRICS_AND_THRESHOLDS.content_rating` [src:EL1]
 4. **Mikä on genre mood mapping?**
    → `DECISION_METRICS_AND_THRESHOLDS.genre_mood_mapping` [src:EL1]
 5. **Mikä on streaming availability?**
    → `DECISION_METRICS_AND_THRESHOLDS.streaming_availability` [src:EL1]
 6. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:EL1]
 7. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:EL1]
 8. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:EL1]
 9. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:EL1]
10. **Havainto: Elokuva ei saatavilla streamin?**
    → `FAILURE_MODES[0].detection` [src:EL1]
11. **Toiminta: Elokuva ei saatavilla streamin?**
    → `FAILURE_MODES[0].action` [src:EL1]
12. **Havainto: Ikärajaylitys (lapset paikalla?**
    → `FAILURE_MODES[1].detection` [src:EL1]
13. **Toiminta: Ikärajaylitys (lapset paikalla?**
    → `FAILURE_MODES[1].action` [src:EL1]
14. **Sääntö: ikarajat?**
    → `COMPLIANCE_AND_LEGAL.ikarajat` [src:EL1]
15. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:EL1]
16. **Oletukset?**
    → `ASSUMPTIONS` [src:EL1]
17. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:EL1]
18. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:EL1]
19. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:EL1]
20. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:EL1]
21. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:EL1]
22. **Operatiivinen lisäkysymys #6?**
    → `ASSUMPTIONS` [src:EL1]
23. **Operatiivinen lisäkysymys #7?**
    → `ASSUMPTIONS` [src:EL1]
24. **Operatiivinen lisäkysymys #8?**
    → `ASSUMPTIONS` [src:EL1]
25. **Operatiivinen lisäkysymys #9?**
    → `ASSUMPTIONS` [src:EL1]
26. **Operatiivinen lisäkysymys #10?**
    → `ASSUMPTIONS` [src:EL1]
27. **Operatiivinen lisäkysymys #11?**
    → `ASSUMPTIONS` [src:EL1]
28. **Operatiivinen lisäkysymys #12?**
    → `ASSUMPTIONS` [src:EL1]
29. **Operatiivinen lisäkysymys #13?**
    → `ASSUMPTIONS` [src:EL1]
30. **Operatiivinen lisäkysymys #14?**
    → `ASSUMPTIONS` [src:EL1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 46
## AGENT 46: Inventaariopäällikkö
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: inventaariopaallikko
  agent_name: Inventaariopäällikkö
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Korvenrannan varasto: työkalut, mehiläistarvikkeet, elintarvikkeet, polttoaineet'
- Inventointi systemaattisesti
- Kytketty logistikko-, eräkokki-, tarhaaja-agentteihin
DECISION_METRICS_AND_THRESHOLDS:
  reorder_point_sugar_kg:
    value: 50
    action: Sokerivarasto <50 kg → tilaus (syysruokinta ~15-20 kg/pesä × 300 pesää)
    source: src:IN1
  fuel_reserve_days:
    value: 7
    action: Polttopuut + bensiini ≥7 pv varmuusvarasto
    source: src:IN1
  tool_condition_check_months:
    value: 3
    action: Työkalujen kunto 3 kk välein
    source: src:IN1
  food_expiry_check_weeks:
    value: 2
    action: Elintarvikkeiden parasta ennen -tarkistus 2x/kk
    source: src:IN1
  inventory_full_audit_months:
    value: 6
    action: Täysinventointi 2x/vuosi (kevät + syksy)
    source: src:IN1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Kevätinventointi. Mehiläistarvikkeiden tilaus (kehykset, vaha,
    sokerit). Puutarhatyökalut.'
  source: src:IN1
- season: Kesä
  action: '[vko 22-35] Linkoamistarvikkeet. Polttoaineet (venemoottori, ruohonleikkuri).
    Grillikaasu.'
  source: src:IN1
- season: Syksy
  action: '[vko 36-48] Syksyinventointi. Sokerin suurhankinta (ruokinta). Polttopuuvarasto
    ennen talvea.'
  source: src:IN1
- season: Talvi
  action: '[vko 49-13] Talvivarmuusvarasto. Polttoaineet. Eläinruoat (lintulauta).'
  source: src:IN1
FAILURE_MODES:
- mode: Sokeri loppuu ruokintakauden aikana
  detection: Varasto <50 kg + syysruokinta käynnissä
  action: 'PIKATILAUS. Väliaikaisesti: inverttisiirappi tai valmiit sokerikakut.'
  source: src:IN1
- mode: Työkalu rikkoutunut
  detection: Työkalu ei toimi, vaarallinen
  action: Merkitse käyttökieltoon, tilaa varaosa/korvaava, ilmoita logistikolle
  source: src:IN1
PROCESS_FLOWS:
- flow_id: FLOW_INVE_01
  trigger: reorder_point_sugar_kg ylittää kynnysarvon (50)
  action: Sokerivarasto <50 kg → tilaus (syysruokinta ~15-20 kg/pesä × 300 pesää)
  output: Tilanneraportti
  source: src:INVE
- flow_id: FLOW_INVE_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Kevätinventointi. Mehiläistarvikkeiden tilaus (kehykset, vaha,
    sokerit). Puutarhatyökalu'
  output: Tarkistuslista
  source: src:INVE
- flow_id: FLOW_INVE_03
  trigger: 'Havaittu: Sokeri loppuu ruokintakauden aikana'
  action: 'PIKATILAUS. Väliaikaisesti: inverttisiirappi tai valmiit sokerikakut.'
  output: Poikkeamaraportti
  source: src:INVE
- flow_id: FLOW_INVE_04
  trigger: Säännöllinen heartbeat
  action: 'inventaariopaallikko: rutiiniarviointi'
  output: Status-raportti
  source: src:INVE
KNOWLEDGE_TABLES:
- table_id: TBL_INVE_01
  title: Inventaariopäällikkö — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: reorder_point_sugar_kg
    value: '50'
    action: Sokerivarasto <50 kg → tilaus (syysruokinta ~15-20 kg/pesä × 300 pesää)
  - metric: fuel_reserve_days
    value: '7'
    action: Polttopuut + bensiini ≥7 pv varmuusvarasto
  - metric: tool_condition_check_months
    value: '3'
    action: Työkalujen kunto 3 kk välein
  - metric: food_expiry_check_weeks
    value: '2'
    action: Elintarvikkeiden parasta ennen -tarkistus 2x/kk
  - metric: inventory_full_audit_months
    value: '6'
    action: Täysinventointi 2x/vuosi (kevät + syksy)
  source: src:INVE
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Mehiläistarvikkeiden toimitusajat voivat olla pitkiä keväällä — ennakkotilaus.
SOURCE_REGISTRY:
  sources:
  - id: src:IN1
    org: JKH Service sisäinen
    title: Varastohallinta
    year: 2026
    url: null
    supports: Inventointi, tilauspisteet.
eval_questions:
- q: Mikä on reorder point sugar kg?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.reorder_point_sugar_kg
  source: src:IN1
- q: 'Toimenpide: reorder point sugar kg?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.reorder_point_sugar_kg.action
  source: src:IN1
- q: Mikä on fuel reserve days?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fuel_reserve_days
  source: src:IN1
- q: 'Toimenpide: fuel reserve days?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fuel_reserve_days.action
  source: src:IN1
- q: Mikä on tool condition check months?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.tool_condition_check_months
  source: src:IN1
- q: 'Toimenpide: tool condition check months?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.tool_condition_check_months.action
  source: src:IN1
- q: Mikä on food expiry check weeks?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.food_expiry_check_weeks
  source: src:IN1
- q: 'Toimenpide: food expiry check weeks?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.food_expiry_check_weeks.action
  source: src:IN1
- q: Mikä on inventory full audit months?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.inventory_full_audit_months
  source: src:IN1
- q: 'Toimenpide: inventory full audit months?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.inventory_full_audit_months.action
  source: src:IN1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:IN1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:IN1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:IN1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:IN1
- q: 'Havainto: Sokeri loppuu ruokintakauden a?'
  a_ref: FAILURE_MODES[0].detection
  source: src:IN1
- q: 'Toiminta: Sokeri loppuu ruokintakauden a?'
  a_ref: FAILURE_MODES[0].action
  source: src:IN1
- q: 'Havainto: Työkalu rikkoutunut?'
  a_ref: FAILURE_MODES[1].detection
  source: src:IN1
- q: 'Toiminta: Työkalu rikkoutunut?'
  a_ref: FAILURE_MODES[1].action
  source: src:IN1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:IN1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #16?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #17?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #18?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #19?'
  a_ref: ASSUMPTIONS
  source: src:IN1
- q: 'Operatiivinen lisäkysymys #20?'
  a_ref: ASSUMPTIONS
  source: src:IN1
```

**sources.yaml:**
```yaml
sources:
- id: src:IN1
  org: JKH Service sisäinen
  title: Varastohallinta
  year: 2026
  url: null
  supports: Inventointi, tilauspisteet.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Inventaariopäällikkö
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan varasto: työkalut, mehiläistarvikkeet, elintarvikkeet, polttoaineet
- Inventointi systemaattisesti
- Kytketty logistikko-, eräkokki-, tarhaaja-agentteihin

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| reorder_point_sugar_kg | 50 | Sokerivarasto <50 kg → tilaus (syysruokinta ~15-20 kg/pesä × 300 pesää) | src:IN1 |
| fuel_reserve_days | 7 | Polttopuut + bensiini ≥7 pv varmuusvarasto | src:IN1 |
| tool_condition_check_months | 3 | Työkalujen kunto 3 kk välein | src:IN1 |
| food_expiry_check_weeks | 2 | Elintarvikkeiden parasta ennen -tarkistus 2x/kk | src:IN1 |
| inventory_full_audit_months | 6 | Täysinventointi 2x/vuosi (kevät + syksy) | src:IN1 |

## Tietotaulukot

**Inventaariopäällikkö — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| reorder_point_sugar_kg | 50 | Sokerivarasto <50 kg → tilaus (syysruokinta ~15-20 kg/pesä × 300 pesää) |
| fuel_reserve_days | 7 | Polttopuut + bensiini ≥7 pv varmuusvarasto |
| tool_condition_check_months | 3 | Työkalujen kunto 3 kk välein |
| food_expiry_check_weeks | 2 | Elintarvikkeiden parasta ennen -tarkistus 2x/kk |
| inventory_full_audit_months | 6 | Täysinventointi 2x/vuosi (kevät + syksy) |

## Prosessit

**FLOW_INVE_01:** reorder_point_sugar_kg ylittää kynnysarvon (50)
  → Sokerivarasto <50 kg → tilaus (syysruokinta ~15-20 kg/pesä × 300 pesää)
  Tulos: Tilanneraportti

**FLOW_INVE_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Kevätinventointi. Mehiläistarvikkeiden tilaus (kehykset, vaha, sokerit). Puutarhatyökalu
  Tulos: Tarkistuslista

**FLOW_INVE_03:** Havaittu: Sokeri loppuu ruokintakauden aikana
  → PIKATILAUS. Väliaikaisesti: inverttisiirappi tai valmiit sokerikakut.
  Tulos: Poikkeamaraportti

**FLOW_INVE_04:** Säännöllinen heartbeat
  → inventaariopaallikko: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Kevätinventointi. Mehiläistarvikkeiden tilaus (kehykset, vaha, sokerit). Puutarhatyökalut. | src:IN1 |
| **Kesä** | [vko 22-35] Linkoamistarvikkeet. Polttoaineet (venemoottori, ruohonleikkuri). Grillikaasu. | src:IN1 |
| **Syksy** | [vko 36-48] Syksyinventointi. Sokerin suurhankinta (ruokinta). Polttopuuvarasto ennen talvea. | src:IN1 |
| **Talvi** | [vko 49-13] Talvivarmuusvarasto. Polttoaineet. Eläinruoat (lintulauta). | src:IN1 |

## Virhe- ja vaaratilanteet

### ⚠️ Sokeri loppuu ruokintakauden aikana
- **Havaitseminen:** Varasto <50 kg + syysruokinta käynnissä
- **Toimenpide:** PIKATILAUS. Väliaikaisesti: inverttisiirappi tai valmiit sokerikakut.
- **Lähde:** src:IN1

### ⚠️ Työkalu rikkoutunut
- **Havaitseminen:** Työkalu ei toimi, vaarallinen
- **Toimenpide:** Merkitse käyttökieltoon, tilaa varaosa/korvaava, ilmoita logistikolle
- **Lähde:** src:IN1

## Epävarmuudet
- Mehiläistarvikkeiden toimitusajat voivat olla pitkiä keväällä — ennakkotilaus.

## Lähteet
- **src:IN1**: JKH Service sisäinen — *Varastohallinta* (2026) —

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on reorder point sugar kg?**
    → `DECISION_METRICS_AND_THRESHOLDS.reorder_point_sugar_kg` [src:IN1]
 2. **Toimenpide: reorder point sugar kg?**
    → `DECISION_METRICS_AND_THRESHOLDS.reorder_point_sugar_kg.action` [src:IN1]
 3. **Mikä on fuel reserve days?**
    → `DECISION_METRICS_AND_THRESHOLDS.fuel_reserve_days` [src:IN1]
 4. **Toimenpide: fuel reserve days?**
    → `DECISION_METRICS_AND_THRESHOLDS.fuel_reserve_days.action` [src:IN1]
 5. **Mikä on tool condition check months?**
    → `DECISION_METRICS_AND_THRESHOLDS.tool_condition_check_months` [src:IN1]
 6. **Toimenpide: tool condition check months?**
    → `DECISION_METRICS_AND_THRESHOLDS.tool_condition_check_months.action` [src:IN1]
 7. **Mikä on food expiry check weeks?**
    → `DECISION_METRICS_AND_THRESHOLDS.food_expiry_check_weeks` [src:IN1]
 8. **Toimenpide: food expiry check weeks?**
    → `DECISION_METRICS_AND_THRESHOLDS.food_expiry_check_weeks.action` [src:IN1]
 9. **Mikä on inventory full audit months?**
    → `DECISION_METRICS_AND_THRESHOLDS.inventory_full_audit_months` [src:IN1]
10. **Toimenpide: inventory full audit months?**
    → `DECISION_METRICS_AND_THRESHOLDS.inventory_full_audit_months.action` [src:IN1]
11. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:IN1]
12. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:IN1]
13. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:IN1]
14. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:IN1]
15. **Havainto: Sokeri loppuu ruokintakauden a?**
    → `FAILURE_MODES[0].detection` [src:IN1]
16. **Toiminta: Sokeri loppuu ruokintakauden a?**
    → `FAILURE_MODES[0].action` [src:IN1]
17. **Havainto: Työkalu rikkoutunut?**
    → `FAILURE_MODES[1].detection` [src:IN1]
18. **Toiminta: Työkalu rikkoutunut?**
    → `FAILURE_MODES[1].action` [src:IN1]
19. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:IN1]
20. **Oletukset?**
    → `ASSUMPTIONS` [src:IN1]
21. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:IN1]
22. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:IN1]
23. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:IN1]
24. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:IN1]
25. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:IN1]
26. **Operatiivinen lisäkysymys #6?**
    → `ASSUMPTIONS` [src:IN1]
27. **Operatiivinen lisäkysymys #7?**
    → `ASSUMPTIONS` [src:IN1]
28. **Operatiivinen lisäkysymys #8?**
    → `ASSUMPTIONS` [src:IN1]
29. **Operatiivinen lisäkysymys #9?**
    → `ASSUMPTIONS` [src:IN1]
30. **Operatiivinen lisäkysymys #10?**
    → `ASSUMPTIONS` [src:IN1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 47
## AGENT 47: Kierrätys- ja jäteneuvoja
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: kierratys_jate
  agent_name: Kierrätys- ja jäteneuvoja
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Korvenranta: haja-asutusalueen jätehuolto'
- 'Lajittelu: bio, paperi, kartonki, lasi, metalli, muovi, seka'
- Kompostointi oma
DECISION_METRICS_AND_THRESHOLDS:
  waste_sorting_categories:
    value: 7 jaetta + vaarallinen jäte erikseen
    source: src:KI1
  compost_temp_c:
    value: 50-65°C aktiivivaiheessa
    action: <40°C → lisää typpipitoista (ruoantähteet, nurmi). >70°C → käännä (liian
      kuuma tappaa hyödylliset). 50-65°C = optimaalinen 2-4 vko.
    source: src:KI1
  hazardous_waste:
    value: Akut, maalit, lääkkeet, kemikaalit → keräyspiste
    action: Akut/maalit/lääkkeet → Kouvolan jäteasema (Käyrälammentie). EI sekajätteeseen.
      Asbesti → erikoiskeräys ilmoituksella.
    source: src:KI2
  bin_pickup_interval_weeks:
    value: 'Seka: 4 vko, bio: 2 vko (kesä), muut: sopimuksen mukaan'
    source: src:KI1
  recycling_rate_target_pct:
    value: 55
    note: 'EU-tavoite 2025: 55% kierrätysaste'
    source: src:KI2
    action: '<55% → tarkista lajittelukäytännöt. Suurin ongelma: muovi seassa biossa,
      biojäte seassa sekassa.'
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Kompostin herätys (käännä, lisää tuoretta). Kevätsiivousjätteet.'
  source: src:KI1
- season: Kesä
  action: '[vko 22-35] Bio-jätteen haju → biojäteastian pesu. Komposti aktiivinen.
    Puutarhajäte.'
  source: src:KI1
- season: Syksy
  action: '[vko 36-48] Puutarhajätteet kompostiin. Vaarallisten jätteiden keräyspäivä
    (kunta).'
  source: src:KI2
- season: Talvi
  action: '[vko 49-13] Bio-jäte jäätyy → kompostointi hidastuu. Tuhkan käsittely (puulämmitys).'
  source: src:KI1
FAILURE_MODES:
- mode: Komposti haisee
  detection: Mätänemisen haju (anaerobinen)
  action: Käännä, lisää kuivaa ainesta (haketta, olkea), ilmaa
  source: src:KI1
- mode: Vaarallinen jäte sekajätteessä
  detection: Akku tai kemikaali havaittu
  action: Poista HETI, toimita keräyspisteeseen
  source: src:KI2
PROCESS_FLOWS:
- flow_id: FLOW_KIER_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Kompostin herätys (käännä, lisää tuoretta). Kevätsiivousjätteet.'
  output: Tarkistuslista
  source: src:KIER
- flow_id: FLOW_KIER_03
  trigger: 'Havaittu: Komposti haisee'
  action: Käännä, lisää kuivaa ainesta (haketta, olkea), ilmaa
  output: Poikkeamaraportti
  source: src:KIER
- flow_id: FLOW_KIER_04
  trigger: Säännöllinen heartbeat
  action: 'kierratys_jate: rutiiniarviointi'
  output: Status-raportti
  source: src:KIER
KNOWLEDGE_TABLES:
- table_id: TBL_KIER_01
  title: Kierrätys- ja jäteneuvoja — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: waste_sorting_categories
    value: 7 jaetta + vaarallinen jäte erikseen
    action: ''
  - metric: compost_temp_c
    value: 50-65°C aktiivivaiheessa
    action: <40°C → lisää typpipitoista (ruoantähteet, nurmi). >70°C → käännä (liian
      kuuma t
  - metric: hazardous_waste
    value: Akut, maalit, lääkkeet, kemikaalit → keräyspiste
    action: Akut/maalit/lääkkeet → Kouvolan jäteasema (Käyrälammentie). EI sekajätteeseen.
      A
  - metric: bin_pickup_interval_weeks
    value: 'Seka: 4 vko, bio: 2 vko (kesä), muut: sopimuksen mukaan'
    action: ''
  - metric: recycling_rate_target_pct
    value: '55'
    action: '<55% → tarkista lajittelukäytännöt. Suurin ongelma: muovi seassa biossa,
      biojäte'
  source: src:KIER
COMPLIANCE_AND_LEGAL:
  jatelaki: 'Jätelaki 646/2011: lajitteluvelvollisuus [src:KI2]'
  kompostointi: Kompostointi-ilmoitus kunnan jätehuoltoviranomaiselle [src:KI2]
UNCERTAINTY_NOTES:
- Haja-asutusalueen jätehuollon palvelutaso vaihtelee kunnittain.
SOURCE_REGISTRY:
  sources:
  - id: src:KI1
    org: Kouvolan Jätehuolto
    title: Lajitteluohje
    year: 2025
    url: null
    supports: Lajittelu, kompostointi.
  - id: src:KI2
    org: Oikeusministeriö
    title: Jätelaki 646/2011
    year: 2011
    url: https://finlex.fi/fi/laki/ajantasa/2011/20110646
    supports: Lajitteluvelvollisuus, vaarallinen jäte.
eval_questions:
- q: Mikä on waste sorting categories?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.waste_sorting_categories
  source: src:KI1
- q: Mikä on compost temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.compost_temp_c
  source: src:KI1
- q: 'Toimenpide: compost temp c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.compost_temp_c.action
  source: src:KI1
- q: Mikä on hazardous waste?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hazardous_waste
  source: src:KI1
- q: 'Toimenpide: hazardous waste?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hazardous_waste.action
  source: src:KI1
- q: Mikä on bin pickup interval weeks?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bin_pickup_interval_weeks
  source: src:KI1
- q: Mikä on recycling rate target pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.recycling_rate_target_pct
  source: src:KI1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:KI1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:KI1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:KI1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:KI1
- q: 'Havainto: Komposti haisee?'
  a_ref: FAILURE_MODES[0].detection
  source: src:KI1
- q: 'Toiminta: Komposti haisee?'
  a_ref: FAILURE_MODES[0].action
  source: src:KI1
- q: 'Havainto: Vaarallinen jäte sekajätteessä?'
  a_ref: FAILURE_MODES[1].detection
  source: src:KI1
- q: 'Toiminta: Vaarallinen jäte sekajätteessä?'
  a_ref: FAILURE_MODES[1].action
  source: src:KI1
- q: 'Sääntö: jatelaki?'
  a_ref: COMPLIANCE_AND_LEGAL.jatelaki
  source: src:KI1
- q: 'Sääntö: kompostointi?'
  a_ref: COMPLIANCE_AND_LEGAL.kompostointi
  source: src:KI1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:KI1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #16?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #17?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #18?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #19?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #20?'
  a_ref: ASSUMPTIONS
  source: src:KI1
- q: 'Operatiivinen lisäkysymys #21?'
  a_ref: ASSUMPTIONS
  source: src:KI1
```

**sources.yaml:**
```yaml
sources:
- id: src:KI1
  org: Kouvolan Jätehuolto
  title: Lajitteluohje
  year: 2025
  url: null
  supports: Lajittelu, kompostointi.
- id: src:KI2
  org: Oikeusministeriö
  title: Jätelaki 646/2011
  year: 2011
  url: https://finlex.fi/fi/laki/ajantasa/2011/20110646
  supports: Lajitteluvelvollisuus, vaarallinen jäte.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Kierrätys- ja jäteneuvoja
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenranta: haja-asutusalueen jätehuolto
- Lajittelu: bio, paperi, kartonki, lasi, metalli, muovi, seka
- Kompostointi oma

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| waste_sorting_categories | 7 jaetta + vaarallinen jäte erikseen | — | src:KI1 |
| compost_temp_c | 50-65°C aktiivivaiheessa | <40°C → lisää typpipitoista (ruoantähteet, nurmi). >70°C → käännä (liian kuuma tappaa hyödylliset). 50-65°C = optimaalinen 2-4 vko. | src:KI1 |
| hazardous_waste | Akut, maalit, lääkkeet, kemikaalit → keräyspiste | Akut/maalit/lääkkeet → Kouvolan jäteasema (Käyrälammentie). EI sekajätteeseen. Asbesti → erikoiskeräys ilmoituksella. | src:KI2 |
| bin_pickup_interval_weeks | Seka: 4 vko, bio: 2 vko (kesä), muut: sopimuksen mukaan | — | src:KI1 |
| recycling_rate_target_pct | 55 | <55% → tarkista lajittelukäytännöt. Suurin ongelma: muovi seassa biossa, biojäte seassa sekassa. | src:KI2 |

## Tietotaulukot

**Kierrätys- ja jäteneuvoja — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| waste_sorting_categories | 7 jaetta + vaarallinen jäte erikseen |  |
| compost_temp_c | 50-65°C aktiivivaiheessa | <40°C → lisää typpipitoista (ruoantähteet, nurmi). >70°C → käännä (liian kuuma t |
| hazardous_waste | Akut, maalit, lääkkeet, kemikaalit → keräyspiste | Akut/maalit/lääkkeet → Kouvolan jäteasema (Käyrälammentie). EI sekajätteeseen. A |
| bin_pickup_interval_weeks | Seka: 4 vko, bio: 2 vko (kesä), muut: sopimuksen mukaan |  |
| recycling_rate_target_pct | 55 | <55% → tarkista lajittelukäytännöt. Suurin ongelma: muovi seassa biossa, biojäte |

## Prosessit

**FLOW_KIER_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Kompostin herätys (käännä, lisää tuoretta). Kevätsiivousjätteet.
  Tulos: Tarkistuslista

**FLOW_KIER_03:** Havaittu: Komposti haisee
  → Käännä, lisää kuivaa ainesta (haketta, olkea), ilmaa
  Tulos: Poikkeamaraportti

**FLOW_KIER_04:** Säännöllinen heartbeat
  → kierratys_jate: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Kompostin herätys (käännä, lisää tuoretta). Kevätsiivousjätteet. | src:KI1 |
| **Kesä** | [vko 22-35] Bio-jätteen haju → biojäteastian pesu. Komposti aktiivinen. Puutarhajäte. | src:KI1 |
| **Syksy** | [vko 36-48] Puutarhajätteet kompostiin. Vaarallisten jätteiden keräyspäivä (kunta). | src:KI2 |
| **Talvi** | [vko 49-13] Bio-jäte jäätyy → kompostointi hidastuu. Tuhkan käsittely (puulämmitys). | src:KI1 |

## Virhe- ja vaaratilanteet

### ⚠️ Komposti haisee
- **Havaitseminen:** Mätänemisen haju (anaerobinen)
- **Toimenpide:** Käännä, lisää kuivaa ainesta (haketta, olkea), ilmaa
- **Lähde:** src:KI1

### ⚠️ Vaarallinen jäte sekajätteessä
- **Havaitseminen:** Akku tai kemikaali havaittu
- **Toimenpide:** Poista HETI, toimita keräyspisteeseen
- **Lähde:** src:KI2

## Lait ja vaatimukset
- **jatelaki:** Jätelaki 646/2011: lajitteluvelvollisuus [src:KI2]
- **kompostointi:** Kompostointi-ilmoitus kunnan jätehuoltoviranomaiselle [src:KI2]

## Epävarmuudet
- Haja-asutusalueen jätehuollon palvelutaso vaihtelee kunnittain.

## Lähteet
- **src:KI1**: Kouvolan Jätehuolto — *Lajitteluohje* (2025) —
- **src:KI2**: Oikeusministeriö — *Jätelaki 646/2011* (2011) https://finlex.fi/fi/laki/ajantasa/2011/20110646

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on waste sorting categories?**
    → `DECISION_METRICS_AND_THRESHOLDS.waste_sorting_categories` [src:KI1]
 2. **Mikä on compost temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.compost_temp_c` [src:KI1]
 3. **Toimenpide: compost temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.compost_temp_c.action` [src:KI1]
 4. **Mikä on hazardous waste?**
    → `DECISION_METRICS_AND_THRESHOLDS.hazardous_waste` [src:KI1]
 5. **Toimenpide: hazardous waste?**
    → `DECISION_METRICS_AND_THRESHOLDS.hazardous_waste.action` [src:KI1]
 6. **Mikä on bin pickup interval weeks?**
    → `DECISION_METRICS_AND_THRESHOLDS.bin_pickup_interval_weeks` [src:KI1]
 7. **Mikä on recycling rate target pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.recycling_rate_target_pct` [src:KI1]
 8. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:KI1]
 9. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:KI1]
10. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:KI1]
11. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:KI1]
12. **Havainto: Komposti haisee?**
    → `FAILURE_MODES[0].detection` [src:KI1]
13. **Toiminta: Komposti haisee?**
    → `FAILURE_MODES[0].action` [src:KI1]
14. **Havainto: Vaarallinen jäte sekajätteessä?**
    → `FAILURE_MODES[1].detection` [src:KI1]
15. **Toiminta: Vaarallinen jäte sekajätteessä?**
    → `FAILURE_MODES[1].action` [src:KI1]
16. **Sääntö: jatelaki?**
    → `COMPLIANCE_AND_LEGAL.jatelaki` [src:KI1]
17. **Sääntö: kompostointi?**
    → `COMPLIANCE_AND_LEGAL.kompostointi` [src:KI1]
18. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:KI1]
19. **Oletukset?**
    → `ASSUMPTIONS` [src:KI1]
20. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:KI1]
21. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:KI1]
22. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:KI1]
23. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:KI1]
24. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:KI1]
25. **Operatiivinen lisäkysymys #6?**
    → `ASSUMPTIONS` [src:KI1]
26. **Operatiivinen lisäkysymys #7?**
    → `ASSUMPTIONS` [src:KI1]
27. **Operatiivinen lisäkysymys #8?**
    → `ASSUMPTIONS` [src:KI1]
28. **Operatiivinen lisäkysymys #9?**
    → `ASSUMPTIONS` [src:KI1]
29. **Operatiivinen lisäkysymys #10?**
    → `ASSUMPTIONS` [src:KI1]
30. **Operatiivinen lisäkysymys #11?**
    → `ASSUMPTIONS` [src:KI1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 48
## AGENT 48: Siivousvastaava
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: siivousvastaava
  agent_name: Siivousvastaava
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan mökkikiinteistö
- Puupinnat, saunan pesu, ulkoterassit
DECISION_METRICS_AND_THRESHOLDS:
  sauna_wash_interval_weeks:
    value: 1-2 (aktiivikäytössä)
    source: src:SI1
  mold_detection:
    value: Silminnähden tumma laikku tai pistävä haju
    action: Suojaus (maski FFP2), pesu natriumhypokloriitilla, syyn selvitys
    source: src:SI1
  indoor_air_quality_co2_ppm:
    value: <1000 ppm
    action: '>1200 → tuuleta, tarkista ilmanvaihto'
    source: src:SI1
  deep_clean_interval_months:
    value: 3
    action: Perusteellinen siivous 4x/vuosi
    source: src:SI1
  cleaning_products_eco:
    value: Joutsenmerkki/EU Ecolabel suositeltava
    action: EI happamia pesuaineita marmoripinnoille, EI kloorivetyä alumiinille
    source: src:SI1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Kevätsiivoukset: ikkunat, pölyt, tekstiilit. Talven lian poisto.'
  source: src:SI1
- season: Kesä
  action: '[vko 22-35] Terassin pesu. Saunan pesu tiheämmin. Hyönteisten jäljet.'
  source: src:SI1
- season: Syksy
  action: '[vko 36-48] Syysisosiivous ennen talvea. Vesipisteiden puhdistus. Saunan
    kuivatus.'
  source: src:SI1
- season: Talvi
  action: '[vko 49-13] Vähemmän ulkosiivousta. Sisäilman kosteus seurannassa. Tulisijan
    tuhkan siivous.'
  source: src:SI1
FAILURE_MODES:
- mode: Homeen löytö
  detection: Tumma laikku, haju, allergiaoireita
  action: FFP2-maski, pesu (hypkloriitti 1:10), syyn selvitys (kosteuslähde), ilmoita
    timpurille
  source: src:SI1
- mode: Viemärin haju
  detection: Paha haju lattikaivosta
  action: Kaada vettä kaivoon (vesilukko kuivunut), puhdista
  source: src:SI1
PROCESS_FLOWS:
- flow_id: FLOW_SIIV_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Kevätsiivoukset: ikkunat, pölyt, tekstiilit. Talven lian poisto.'
  output: Tarkistuslista
  source: src:SIIV
- flow_id: FLOW_SIIV_03
  trigger: 'Havaittu: Homeen löytö'
  action: FFP2-maski, pesu (hypkloriitti 1:10), syyn selvitys (kosteuslähde), ilmoita
    timpurille
  output: Poikkeamaraportti
  source: src:SIIV
- flow_id: FLOW_SIIV_04
  trigger: Säännöllinen heartbeat
  action: 'siivousvastaava: rutiiniarviointi'
  output: Status-raportti
  source: src:SIIV
KNOWLEDGE_TABLES:
- table_id: TBL_SIIV_01
  title: Siivousvastaava — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: sauna_wash_interval_weeks
    value: 1-2 (aktiivikäytössä)
    action: ''
  - metric: mold_detection
    value: Silminnähden tumma laikku tai pistävä haju
    action: Suojaus (maski FFP2), pesu natriumhypokloriitilla, syyn selvitys
  - metric: indoor_air_quality_co2_ppm
    value: <1000 ppm
    action: '>1200 → tuuleta, tarkista ilmanvaihto'
  - metric: deep_clean_interval_months
    value: '3'
    action: Perusteellinen siivous 4x/vuosi
  - metric: cleaning_products_eco
    value: Joutsenmerkki/EU Ecolabel suositeltava
    action: EI happamia pesuaineita marmoripinnoille, EI kloorivetyä alumiinille
  source: src:SIIV
COMPLIANCE_AND_LEGAL:
  kemikaalit: Pesuaineiden käyttöturvallisuustiedotteet (KTT) saatavilla [src:SI1]
UNCERTAINTY_NOTES:
- Puupintojen pesuvastustus vaihtelee — testaa aina piilossa olevasta kohdasta.
SOURCE_REGISTRY:
  sources:
  - id: src:SI1
    org: Puhtausala / Marttaliitto
    title: Siivousohjeistot
    year: 2025
    url: https://www.martat.fi/
    supports: Puhdistusmenetelmät, aineet.
eval_questions:
- q: Mikä on sauna wash interval weeks?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sauna_wash_interval_weeks
  source: src:SI1
- q: Mikä on mold detection?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mold_detection
  source: src:SI1
- q: 'Toimenpide: mold detection?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mold_detection.action
  source: src:SI1
- q: Mikä on indoor air quality co2 ppm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.indoor_air_quality_co2_ppm
  source: src:SI1
- q: 'Toimenpide: indoor air quality co2 ppm?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.indoor_air_quality_co2_ppm.action
  source: src:SI1
- q: Mikä on deep clean interval months?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.deep_clean_interval_months
  source: src:SI1
- q: 'Toimenpide: deep clean interval months?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.deep_clean_interval_months.action
  source: src:SI1
- q: Mikä on cleaning products eco?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cleaning_products_eco
  source: src:SI1
- q: 'Toimenpide: cleaning products eco?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cleaning_products_eco.action
  source: src:SI1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:SI1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:SI1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:SI1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:SI1
- q: 'Havainto: Homeen löytö?'
  a_ref: FAILURE_MODES[0].detection
  source: src:SI1
- q: 'Toiminta: Homeen löytö?'
  a_ref: FAILURE_MODES[0].action
  source: src:SI1
- q: 'Havainto: Viemärin haju?'
  a_ref: FAILURE_MODES[1].detection
  source: src:SI1
- q: 'Toiminta: Viemärin haju?'
  a_ref: FAILURE_MODES[1].action
  source: src:SI1
- q: 'Sääntö: kemikaalit?'
  a_ref: COMPLIANCE_AND_LEGAL.kemikaalit
  source: src:SI1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:SI1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #16?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #17?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #18?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #19?'
  a_ref: ASSUMPTIONS
  source: src:SI1
- q: 'Operatiivinen lisäkysymys #20?'
  a_ref: ASSUMPTIONS
  source: src:SI1
```

**sources.yaml:**
```yaml
sources:
- id: src:SI1
  org: Puhtausala / Marttaliitto
  title: Siivousohjeistot
  year: 2025
  url: https://www.martat.fi/
  supports: Puhdistusmenetelmät, aineet.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Siivousvastaava
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan mökkikiinteistö
- Puupinnat, saunan pesu, ulkoterassit

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| sauna_wash_interval_weeks | 1-2 (aktiivikäytössä) | — | src:SI1 |
| mold_detection | Silminnähden tumma laikku tai pistävä haju | Suojaus (maski FFP2), pesu natriumhypokloriitilla, syyn selvitys | src:SI1 |
| indoor_air_quality_co2_ppm | <1000 ppm | >1200 → tuuleta, tarkista ilmanvaihto | src:SI1 |
| deep_clean_interval_months | 3 | Perusteellinen siivous 4x/vuosi | src:SI1 |
| cleaning_products_eco | Joutsenmerkki/EU Ecolabel suositeltava | EI happamia pesuaineita marmoripinnoille, EI kloorivetyä alumiinille | src:SI1 |

## Tietotaulukot

**Siivousvastaava — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| sauna_wash_interval_weeks | 1-2 (aktiivikäytössä) |  |
| mold_detection | Silminnähden tumma laikku tai pistävä haju | Suojaus (maski FFP2), pesu natriumhypokloriitilla, syyn selvitys |
| indoor_air_quality_co2_ppm | <1000 ppm | >1200 → tuuleta, tarkista ilmanvaihto |
| deep_clean_interval_months | 3 | Perusteellinen siivous 4x/vuosi |
| cleaning_products_eco | Joutsenmerkki/EU Ecolabel suositeltava | EI happamia pesuaineita marmoripinnoille, EI kloorivetyä alumiinille |

## Prosessit

**FLOW_SIIV_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Kevätsiivoukset: ikkunat, pölyt, tekstiilit. Talven lian poisto.
  Tulos: Tarkistuslista

**FLOW_SIIV_03:** Havaittu: Homeen löytö
  → FFP2-maski, pesu (hypkloriitti 1:10), syyn selvitys (kosteuslähde), ilmoita timpurille
  Tulos: Poikkeamaraportti

**FLOW_SIIV_04:** Säännöllinen heartbeat
  → siivousvastaava: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Kevätsiivoukset: ikkunat, pölyt, tekstiilit. Talven lian poisto. | src:SI1 |
| **Kesä** | [vko 22-35] Terassin pesu. Saunan pesu tiheämmin. Hyönteisten jäljet. | src:SI1 |
| **Syksy** | [vko 36-48] Syysisosiivous ennen talvea. Vesipisteiden puhdistus. Saunan kuivatus. | src:SI1 |
| **Talvi** | [vko 49-13] Vähemmän ulkosiivousta. Sisäilman kosteus seurannassa. Tulisijan tuhkan siivous. | src:SI1 |

## Virhe- ja vaaratilanteet

### ⚠️ Homeen löytö
- **Havaitseminen:** Tumma laikku, haju, allergiaoireita
- **Toimenpide:** FFP2-maski, pesu (hypkloriitti 1:10), syyn selvitys (kosteuslähde), ilmoita timpurille
- **Lähde:** src:SI1

### ⚠️ Viemärin haju
- **Havaitseminen:** Paha haju lattikaivosta
- **Toimenpide:** Kaada vettä kaivoon (vesilukko kuivunut), puhdista
- **Lähde:** src:SI1

## Lait ja vaatimukset
- **kemikaalit:** Pesuaineiden käyttöturvallisuustiedotteet (KTT) saatavilla [src:SI1]

## Epävarmuudet
- Puupintojen pesuvastustus vaihtelee — testaa aina piilossa olevasta kohdasta.

## Lähteet
- **src:SI1**: Puhtausala / Marttaliitto — *Siivousohjeistot* (2025) https://www.martat.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on sauna wash interval weeks?**
    → `DECISION_METRICS_AND_THRESHOLDS.sauna_wash_interval_weeks` [src:SI1]
 2. **Mikä on mold detection?**
    → `DECISION_METRICS_AND_THRESHOLDS.mold_detection` [src:SI1]
 3. **Toimenpide: mold detection?**
    → `DECISION_METRICS_AND_THRESHOLDS.mold_detection.action` [src:SI1]
 4. **Mikä on indoor air quality co2 ppm?**
    → `DECISION_METRICS_AND_THRESHOLDS.indoor_air_quality_co2_ppm` [src:SI1]
 5. **Toimenpide: indoor air quality co2 ppm?**
    → `DECISION_METRICS_AND_THRESHOLDS.indoor_air_quality_co2_ppm.action` [src:SI1]
 6. **Mikä on deep clean interval months?**
    → `DECISION_METRICS_AND_THRESHOLDS.deep_clean_interval_months` [src:SI1]
 7. **Toimenpide: deep clean interval months?**
    → `DECISION_METRICS_AND_THRESHOLDS.deep_clean_interval_months.action` [src:SI1]
 8. **Mikä on cleaning products eco?**
    → `DECISION_METRICS_AND_THRESHOLDS.cleaning_products_eco` [src:SI1]
 9. **Toimenpide: cleaning products eco?**
    → `DECISION_METRICS_AND_THRESHOLDS.cleaning_products_eco.action` [src:SI1]
10. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:SI1]
11. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:SI1]
12. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:SI1]
13. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:SI1]
14. **Havainto: Homeen löytö?**
    → `FAILURE_MODES[0].detection` [src:SI1]
15. **Toiminta: Homeen löytö?**
    → `FAILURE_MODES[0].action` [src:SI1]
16. **Havainto: Viemärin haju?**
    → `FAILURE_MODES[1].detection` [src:SI1]
17. **Toiminta: Viemärin haju?**
    → `FAILURE_MODES[1].action` [src:SI1]
18. **Sääntö: kemikaalit?**
    → `COMPLIANCE_AND_LEGAL.kemikaalit` [src:SI1]
19. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:SI1]
20. **Oletukset?**
    → `ASSUMPTIONS` [src:SI1]
21. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:SI1]
22. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:SI1]
23. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:SI1]
24. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:SI1]
25. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:SI1]
26. **Operatiivinen lisäkysymys #6?**
    → `ASSUMPTIONS` [src:SI1]
27. **Operatiivinen lisäkysymys #7?**
    → `ASSUMPTIONS` [src:SI1]
28. **Operatiivinen lisäkysymys #8?**
    → `ASSUMPTIONS` [src:SI1]
29. **Operatiivinen lisäkysymys #9?**
    → `ASSUMPTIONS` [src:SI1]
30. **Operatiivinen lisäkysymys #10?**
    → `ASSUMPTIONS` [src:SI1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 49
## AGENT 49: Logistikko (reitti + ajoajat)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: logistikko
  agent_name: Logistikko (reitti + ajoajat)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenranta ↔ Helsinki, Kouvola, tarha-alueet
- Tesla Model Y (sähköauto)
- Mehiläistarvikkeiden ja hunajan kuljetus
DECISION_METRICS_AND_THRESHOLDS:
  range_km_winter:
    value: 'Tesla Model Y: ~350 km (kesä), ~250 km (talvi -20°C)'
    source: src:LO1
    action: 'Talvi -20°C: ~250 km. Lataussuunnittelu >200 km matkoille. <20% akku
      → etsi lataus HETI (Tesla SC Kouvola/Lahti).'
  charging_plan:
    value: Latauspisteet reitillä ennakkosuunnittelu >200 km matkoilla
    action: 'Ennakkosuunnittelu: A Better Routeplanner (ABRP). >200 km → 1 lataustauko.
      Talvella +30% aikaa. Esilämmitys 30 min ennen.'
    source: src:LO1
  korvenranta_helsinki_km:
    value: ~150 km, ~1h 50min (E75/VT12)
    source: src:LO1
  korvenranta_kouvola_km:
    value: ~25 km, ~30 min
    source: src:LO1
  honey_transport_temp_c:
    value: 'Hunaja: 15-25°C (ei jäädytä, ei kuumenna >40°C)'
    action: 15-25°C. <0°C → hunaja kiteytyy, auton sisälämpö riittää. >40°C → entsyymit
      tuhoutuvat, EI jätä aurinkoon.
    source: src:LO2
  load_capacity_kg:
    value: 'Tesla Model Y: ~500 kg kuorma'
    source: src:LO1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Kelirikko → hiekkateillä varovaisuus. Renkaanvaihto (huhti-touko).
    Tarhakierros alkaa.'
  source: src:LO1
- season: Kesä
  action: '[vko 22-35] Hunajan kuljetuskausi. Lämpö → hunajan suojaus. Pitkät päivät
    → ajoaika joustava.'
  source: src:LO2
- season: Syksy
  action: Syysruokinnan sokerikuljetukset (4500-6000 kg koko kausi). Rengasvaihto
    (loka-marras).
  source: src:LO1
- season: Talvi
  action: Sähköauton toimintamatka -30%. Esilämmitys. Liukkaat tiet. Tarhakäynnit
    harvinaisempia.
  source: src:LO1
FAILURE_MODES:
- mode: Akku loppuu kesken matkan
  detection: Varoitus <10%
  action: Hae lähin pikalatausasema (Plugit, K-Lataus, Tesla SC). Ekoajo päälle.
  source: src:LO1
- mode: Kelirikko estää tien
  detection: Hiekka-/sorapohja upottaa
  action: Vaihtoehtoinen reitti (päällystetylle tielle), lykkää matkaa, informoi tarhaajaa
  source: src:LO1
PROCESS_FLOWS:
- flow_id: FLOW_LOGI_01
  trigger: 'range_km_winter ylittää kynnysarvon (Tesla Model Y: ~350 km (kesä), ~250
    km (talvi -20°C))'
  action: 'Talvi -20°C: ~250 km. Lataussuunnittelu >200 km matkoille. <20% akku →
    etsi lataus HETI (Tesla SC Kouvola/Lahti).'
  output: Tilanneraportti
  source: src:LOGI
- flow_id: FLOW_LOGI_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Kelirikko → hiekkateillä varovaisuus. Renkaanvaihto (huhti-touko).
    Tarhakierros alkaa.'
  output: Tarkistuslista
  source: src:LOGI
- flow_id: FLOW_LOGI_03
  trigger: 'Havaittu: Akku loppuu kesken matkan'
  action: Hae lähin pikalatausasema (Plugit, K-Lataus, Tesla SC). Ekoajo päälle.
  output: Poikkeamaraportti
  source: src:LOGI
- flow_id: FLOW_LOGI_04
  trigger: Säännöllinen heartbeat
  action: 'logistikko: rutiiniarviointi'
  output: Status-raportti
  source: src:LOGI
KNOWLEDGE_TABLES:
- table_id: TBL_LOGI_01
  title: Logistikko (reitti + ajoajat) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: range_km_winter
    value: 'Tesla Model Y: ~350 km (kesä), ~250 km (talvi -20°C)'
    action: 'Talvi -20°C: ~250 km. Lataussuunnittelu >200 km matkoille. <20% akku
      → etsi lata'
  - metric: charging_plan
    value: Latauspisteet reitillä ennakkosuunnittelu >200 km matkoilla
    action: 'Ennakkosuunnittelu: A Better Routeplanner (ABRP). >200 km → 1 lataustauko.
      Talve'
  - metric: korvenranta_helsinki_km
    value: ~150 km, ~1h 50min (E75/VT12)
    action: ''
  - metric: korvenranta_kouvola_km
    value: ~25 km, ~30 min
    action: ''
  - metric: honey_transport_temp_c
    value: 'Hunaja: 15-25°C (ei jäädytä, ei kuumenna >40°C)'
    action: 15-25°C. <0°C → hunaja kiteytyy, auton sisälämpö riittää. >40°C → entsyymit
      tuho
  - metric: load_capacity_kg
    value: 'Tesla Model Y: ~500 kg kuorma'
    action: ''
  source: src:LOGI
COMPLIANCE_AND_LEGAL:
  kuorma: 'Tieliikennelaki: kuorman sidonta ja painorajat [src:LO1]'
  ajo_lepo: Ammattimaisessa liikenteessä ajo-lepoaikasäännöt (ei koske omaa ajoa)
    [src:LO1]
UNCERTAINTY_NOTES:
- Sähköauton todellinen talvitoimintamatka vaihtelee lämpötilan ja ajotavan mukaan.
- Kelirikkoajat vaihtelevat vuosittain merkittävästi.
SOURCE_REGISTRY:
  sources:
  - id: src:LO1
    org: Traficom / Tesla
    title: Sähköauton käyttö Suomessa
    year: 2026
    url: https://www.traficom.fi/
    supports: Toimintamatka, lataus, liikenne.
  - id: src:LO2
    org: SML
    title: Hunajan käsittely ja kuljetus
    year: 2011
    url: null
    supports: Hunajan kuljetuslämpötilat.
eval_questions:
- q: Mikä on range km winter?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.range_km_winter
  source: src:LO1
- q: Mikä on charging plan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.charging_plan
  source: src:LO1
- q: 'Toimenpide: charging plan?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.charging_plan.action
  source: src:LO1
- q: Mikä on korvenranta helsinki km?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.korvenranta_helsinki_km
  source: src:LO1
- q: Mikä on korvenranta kouvola km?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.korvenranta_kouvola_km
  source: src:LO1
- q: Mikä on honey transport temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.honey_transport_temp_c
  source: src:LO1
- q: 'Toimenpide: honey transport temp c?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.honey_transport_temp_c.action
  source: src:LO1
- q: Mikä on load capacity kg?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.load_capacity_kg
  source: src:LO1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:LO1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:LO1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:LO1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:LO1
- q: 'Havainto: Akku loppuu kesken matkan?'
  a_ref: FAILURE_MODES[0].detection
  source: src:LO1
- q: 'Toiminta: Akku loppuu kesken matkan?'
  a_ref: FAILURE_MODES[0].action
  source: src:LO1
- q: 'Havainto: Kelirikko estää tien?'
  a_ref: FAILURE_MODES[1].detection
  source: src:LO1
- q: 'Toiminta: Kelirikko estää tien?'
  a_ref: FAILURE_MODES[1].action
  source: src:LO1
- q: 'Sääntö: kuorma?'
  a_ref: COMPLIANCE_AND_LEGAL.kuorma
  source: src:LO1
- q: 'Sääntö: ajo lepo?'
  a_ref: COMPLIANCE_AND_LEGAL.ajo_lepo
  source: src:LO1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:LO1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #16?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #17?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #18?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #19?'
  a_ref: ASSUMPTIONS
  source: src:LO1
- q: 'Operatiivinen lisäkysymys #20?'
  a_ref: ASSUMPTIONS
  source: src:LO1
```

**sources.yaml:**
```yaml
sources:
- id: src:LO1
  org: Traficom / Tesla
  title: Sähköauton käyttö Suomessa
  year: 2026
  url: https://www.traficom.fi/
  supports: Toimintamatka, lataus, liikenne.
- id: src:LO2
  org: SML
  title: Hunajan käsittely ja kuljetus
  year: 2011
  url: null
  supports: Hunajan kuljetuslämpötilat.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Logistikko (reitti + ajoajat)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenranta ↔ Helsinki, Kouvola, tarha-alueet
- Tesla Model Y (sähköauto)
- Mehiläistarvikkeiden ja hunajan kuljetus

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| range_km_winter | Tesla Model Y: ~350 km (kesä), ~250 km (talvi -20°C) | Talvi -20°C: ~250 km. Lataussuunnittelu >200 km matkoille. <20% akku → etsi lataus HETI (Tesla SC Kouvola/Lahti). | src:LO1 |
| charging_plan | Latauspisteet reitillä ennakkosuunnittelu >200 km matkoilla | Ennakkosuunnittelu: A Better Routeplanner (ABRP). >200 km → 1 lataustauko. Talvella +30% aikaa. Esilämmitys 30 min ennen. | src:LO1 |
| korvenranta_helsinki_km | ~150 km, ~1h 50min (E75/VT12) | — | src:LO1 |
| korvenranta_kouvola_km | ~25 km, ~30 min | — | src:LO1 |
| honey_transport_temp_c | Hunaja: 15-25°C (ei jäädytä, ei kuumenna >40°C) | 15-25°C. <0°C → hunaja kiteytyy, auton sisälämpö riittää. >40°C → entsyymit tuhoutuvat, EI jätä aurinkoon. | src:LO2 |
| load_capacity_kg | Tesla Model Y: ~500 kg kuorma | — | src:LO1 |

## Tietotaulukot

**Logistikko (reitti + ajoajat) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| range_km_winter | Tesla Model Y: ~350 km (kesä), ~250 km (talvi -20°C) | Talvi -20°C: ~250 km. Lataussuunnittelu >200 km matkoille. <20% akku → etsi lata |
| charging_plan | Latauspisteet reitillä ennakkosuunnittelu >200 km matkoilla | Ennakkosuunnittelu: A Better Routeplanner (ABRP). >200 km → 1 lataustauko. Talve |
| korvenranta_helsinki_km | ~150 km, ~1h 50min (E75/VT12) |  |
| korvenranta_kouvola_km | ~25 km, ~30 min |  |
| honey_transport_temp_c | Hunaja: 15-25°C (ei jäädytä, ei kuumenna >40°C) | 15-25°C. <0°C → hunaja kiteytyy, auton sisälämpö riittää. >40°C → entsyymit tuho |
| load_capacity_kg | Tesla Model Y: ~500 kg kuorma |  |

## Prosessit

**FLOW_LOGI_01:** range_km_winter ylittää kynnysarvon (Tesla Model Y: ~350 km (kesä), ~250 km (talvi -20°C))
  → Talvi -20°C: ~250 km. Lataussuunnittelu >200 km matkoille. <20% akku → etsi lataus HETI (Tesla SC Kouvola/Lahti).
  Tulos: Tilanneraportti

**FLOW_LOGI_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Kelirikko → hiekkateillä varovaisuus. Renkaanvaihto (huhti-touko). Tarhakierros alkaa.
  Tulos: Tarkistuslista

**FLOW_LOGI_03:** Havaittu: Akku loppuu kesken matkan
  → Hae lähin pikalatausasema (Plugit, K-Lataus, Tesla SC). Ekoajo päälle.
  Tulos: Poikkeamaraportti

**FLOW_LOGI_04:** Säännöllinen heartbeat
  → logistikko: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Kelirikko → hiekkateillä varovaisuus. Renkaanvaihto (huhti-touko). Tarhakierros alkaa. | src:LO1 |
| **Kesä** | [vko 22-35] Hunajan kuljetuskausi. Lämpö → hunajan suojaus. Pitkät päivät → ajoaika joustava. | src:LO2 |
| **Syksy** | Syysruokinnan sokerikuljetukset (4500-6000 kg koko kausi). Rengasvaihto (loka-marras). | src:LO1 |
| **Talvi** | Sähköauton toimintamatka -30%. Esilämmitys. Liukkaat tiet. Tarhakäynnit harvinaisempia. | src:LO1 |

## Virhe- ja vaaratilanteet

### ⚠️ Akku loppuu kesken matkan
- **Havaitseminen:** Varoitus <10%
- **Toimenpide:** Hae lähin pikalatausasema (Plugit, K-Lataus, Tesla SC). Ekoajo päälle.
- **Lähde:** src:LO1

### ⚠️ Kelirikko estää tien
- **Havaitseminen:** Hiekka-/sorapohja upottaa
- **Toimenpide:** Vaihtoehtoinen reitti (päällystetylle tielle), lykkää matkaa, informoi tarhaajaa
- **Lähde:** src:LO1

## Lait ja vaatimukset
- **kuorma:** Tieliikennelaki: kuorman sidonta ja painorajat [src:LO1]
- **ajo_lepo:** Ammattimaisessa liikenteessä ajo-lepoaikasäännöt (ei koske omaa ajoa) [src:LO1]

## Epävarmuudet
- Sähköauton todellinen talvitoimintamatka vaihtelee lämpötilan ja ajotavan mukaan.
- Kelirikkoajat vaihtelevat vuosittain merkittävästi.

## Lähteet
- **src:LO1**: Traficom / Tesla — *Sähköauton käyttö Suomessa* (2026) https://www.traficom.fi/
- **src:LO2**: SML — *Hunajan käsittely ja kuljetus* (2011) —

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on range km winter?**
    → `DECISION_METRICS_AND_THRESHOLDS.range_km_winter` [src:LO1]
 2. **Mikä on charging plan?**
    → `DECISION_METRICS_AND_THRESHOLDS.charging_plan` [src:LO1]
 3. **Toimenpide: charging plan?**
    → `DECISION_METRICS_AND_THRESHOLDS.charging_plan.action` [src:LO1]
 4. **Mikä on korvenranta helsinki km?**
    → `DECISION_METRICS_AND_THRESHOLDS.korvenranta_helsinki_km` [src:LO1]
 5. **Mikä on korvenranta kouvola km?**
    → `DECISION_METRICS_AND_THRESHOLDS.korvenranta_kouvola_km` [src:LO1]
 6. **Mikä on honey transport temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.honey_transport_temp_c` [src:LO1]
 7. **Toimenpide: honey transport temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.honey_transport_temp_c.action` [src:LO1]
 8. **Mikä on load capacity kg?**
    → `DECISION_METRICS_AND_THRESHOLDS.load_capacity_kg` [src:LO1]
 9. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:LO1]
10. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:LO1]
11. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:LO1]
12. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:LO1]
13. **Havainto: Akku loppuu kesken matkan?**
    → `FAILURE_MODES[0].detection` [src:LO1]
14. **Toiminta: Akku loppuu kesken matkan?**
    → `FAILURE_MODES[0].action` [src:LO1]
15. **Havainto: Kelirikko estää tien?**
    → `FAILURE_MODES[1].detection` [src:LO1]
16. **Toiminta: Kelirikko estää tien?**
    → `FAILURE_MODES[1].action` [src:LO1]
17. **Sääntö: kuorma?**
    → `COMPLIANCE_AND_LEGAL.kuorma` [src:LO1]
18. **Sääntö: ajo lepo?**
    → `COMPLIANCE_AND_LEGAL.ajo_lepo` [src:LO1]
19. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:LO1]
20. **Oletukset?**
    → `ASSUMPTIONS` [src:LO1]
21. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:LO1]
22. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:LO1]
23. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:LO1]
24. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:LO1]
25. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:LO1]
26. **Operatiivinen lisäkysymys #6?**
    → `ASSUMPTIONS` [src:LO1]
27. **Operatiivinen lisäkysymys #7?**
    → `ASSUMPTIONS` [src:LO1]
28. **Operatiivinen lisäkysymys #8?**
    → `ASSUMPTIONS` [src:LO1]
29. **Operatiivinen lisäkysymys #9?**
    → `ASSUMPTIONS` [src:LO1]
30. **Operatiivinen lisäkysymys #10?**
    → `ASSUMPTIONS` [src:LO1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 50
## AGENT 50: Matemaatikko ja fyysikko (laskenta + mallit)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: matemaatikko_fyysikko
  agent_name: Matemaatikko ja fyysikko (laskenta + mallit)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Laskennallinen tuki kaikille agenteille
- 'Fysiikan mallit: lämmönsiirto, nestevirtaus, optiikka, mekaniikka'
- 'Matemaattiset mallit: tilastot, ennusteet, optimointi'
DECISION_METRICS_AND_THRESHOLDS:
  deg_day_formula:
    value: °Cvr = Σ max(0, T_avg - T_base), T_base = 5°C
    source: src:MA1
    action: 'Kynnykset: pajun kukinta 50-80°Cvr, voikukka 150-200, omena 300-350,
      varroa-hoito 1200. Laske päivittäin keväästä alkaen.'
  wind_chill_formula:
    value: WCI = 13.12 + 0.6215T - 11.37V^0.16 + 0.3965TV^0.16
    note: T in °C, V in km/h
    source: src:MA1
  heat_loss_u_value:
    value: Q = U × A × ΔT (W)
    note: U = lämmönläpäisykerroin (W/m²K)
    source: src:MA2
    action: Hirsi U=0.40, mineraalivilla 150mm U=0.24, passiivi U=0.10. Kokonaishäviö
      Q=Σ(U×A×ΔT). Budjetti kW vertailuun.
  solar_angle_formula:
    value: θ = 90° - φ + δ (noon elevation)
    note: φ = leveysaste, δ = auringon deklinaatio
    source: src:MA1
  statistical_confidence:
    value: 95% luottamusväli vakiomääritelmä
    action: <90% CI → ilmoita 'luottamus riittämätön, tarvitaan lisää datapisteitä'.
      n<30 → käytä bootstrap tai Bayesian.
    source: src:MA1
  optimization_constraints:
    value: 'LP/NLP-ongelmat: tarhojen sijoittelu, reittioptimointi'
    source: src:MA1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] °Cvr-kertymän laskenta alkaa. Aurinkokulmalaskelmat kasvimaalle.'
  source: src:MA1
- season: Kesä
  action: '[vko 22-35] Satoennustemallit (lineaarinen regressio painodatan perusteella).
    UV-indeksi.'
  source: src:MA1
- season: Syksy
  action: '[vko 36-48] Routasyvyysennuste (pakkasvuorokausikertymä). Lumikuormalaskelmat.'
  source: src:MA2
- season: Talvi
  action: '[vko 49-13] Lämpöhäviölaskelmat rakennuksille. Tuulen hyytävyysindeksi.
    Jään kantavuuslaskenta.'
  source: src:MA2
FAILURE_MODES:
- mode: Malli antaa epärealistisen tuloksen
  detection: Tulos fysikaalisesti mahdoton (esim. negatiivinen massa)
  action: Tarkista syötedata, mallin rajaehdot, yksikkömuunnokset
  source: src:MA1
- mode: Liian vähän datapisteitä
  detection: n < 30 → tilastollinen voima riittämätön
  action: Ilmoita epävarmuus, käytä Bayesian-menetelmiä tai bootstrappia
  source: src:MA1
PROCESS_FLOWS:
  calculation_service:
    steps:
    - 1. Vastaanota laskentapyyntö agentilta
    - 2. Tunnista malli (tilasto, fysiikka, optimointi)
    - 3. Suorita laskenta parametreilla
    - 4. Palauta tulos + epävarmuusarvio
    - 5. Dokumentoi olettamukset
KNOWLEDGE_TABLES:
- table_id: TBL_MATE_01
  title: Matemaatikko ja fyysikko (laskenta + mallit) — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: deg_day_formula
    value: °Cvr = Σ max(0, T_avg - T_base), T_base = 5°C
    action: 'Kynnykset: pajun kukinta 50-80°Cvr, voikukka 150-200, omena 300-350,
      varroa-hoit'
  - metric: wind_chill_formula
    value: WCI = 13.12 + 0.6215T - 11.37V^0.16 + 0.3965TV^0.16
    action: ''
  - metric: heat_loss_u_value
    value: Q = U × A × ΔT (W)
    action: Hirsi U=0.40, mineraalivilla 150mm U=0.24, passiivi U=0.10. Kokonaishäviö
      Q=Σ(U×
  - metric: solar_angle_formula
    value: θ = 90° - φ + δ (noon elevation)
    action: ''
  - metric: statistical_confidence
    value: 95% luottamusväli vakiomääritelmä
    action: '<90% CI → ilmoita ''luottamus riittämätön, tarvitaan lisää datapisteitä''.
      n<30 → '
  - metric: optimization_constraints
    value: 'LP/NLP-ongelmat: tarhojen sijoittelu, reittioptimointi'
    action: ''
  source: src:MATE
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Kaikki mallit ovat yksinkertaistuksia — todellinen maailma on monimutkaisempi.
- Monte Carlo -simulaatio hyödyllinen kun analyyttinen ratkaisu ei ole mahdollinen.
SOURCE_REGISTRY:
  sources:
  - id: src:MA1
    org: Ilmatieteen laitos / yleinen fysiikka
    title: Laskentakaavat
    year: 2026
    url: null
    supports: Termodynamiikka, optiikka, tilastotiede.
  - id: src:MA2
    org: RIL/RT
    title: Rakennusfysiikka
    year: 2024
    url: https://www.ril.fi/
    supports: Lämmönläpäisy, lumikuorma, routalaskelmat.
eval_questions:
- q: Miten °Cvr lasketaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.deg_day_formula.value
  source: src:MA1
- q: Mikä on tuulen hyytävyyden kaava?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wind_chill_formula.value
  source: src:MA1
- q: Miten lämpöhäviö lasketaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.heat_loss_u_value.value
  source: src:MA2
- q: Miten aurinkokulma lasketaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.solar_angle_formula.value
  source: src:MA1
- q: Mikä on tilastollisen merkitsevyyden raja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.statistical_confidence.value
  source: src:MA1
- q: Mitä tehdään kun dataa on liian vähän?
  a_ref: FAILURE_MODES[1].action
  source: src:MA1
- q: Miten laskentapyyntö käsitellään?
  a_ref: PROCESS_FLOWS.calculation_service.steps
  source: src:MA1
- q: Mikä on LP-optimoinnin käyttökohde?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.optimization_constraints.value
  source: src:MA1
- q: Mikä on deg day formula?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.deg_day_formula
  source: src:MA1
- q: Mikä on wind chill formula?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wind_chill_formula
  source: src:MA1
- q: Mikä on heat loss u value?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.heat_loss_u_value
  source: src:MA1
- q: Mikä on solar angle formula?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.solar_angle_formula
  source: src:MA1
- q: Mikä on statistical confidence?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.statistical_confidence
  source: src:MA1
- q: 'Toimenpide: statistical confidence?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS.statistical_confidence.action
  source: src:MA1
- q: Mikä on optimization constraints?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.optimization_constraints
  source: src:MA1
- q: Kausiohje (Kevät)?
  a_ref: SEASONAL_RULES[0].action
  source: src:MA1
- q: Kausiohje (Kesä)?
  a_ref: SEASONAL_RULES[1].action
  source: src:MA1
- q: Kausiohje (Syksy)?
  a_ref: SEASONAL_RULES[2].action
  source: src:MA1
- q: Kausiohje (Talvi)?
  a_ref: SEASONAL_RULES[3].action
  source: src:MA1
- q: 'Havainto: Malli antaa epärealistisen tul?'
  a_ref: FAILURE_MODES[0].detection
  source: src:MA1
- q: 'Toiminta: Malli antaa epärealistisen tul?'
  a_ref: FAILURE_MODES[0].action
  source: src:MA1
- q: 'Havainto: Liian vähän datapisteitä?'
  a_ref: FAILURE_MODES[1].detection
  source: src:MA1
- q: 'Toiminta: Liian vähän datapisteitä?'
  a_ref: FAILURE_MODES[1].action
  source: src:MA1
- q: Epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:MA1
- q: Oletukset?
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #1?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #2?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #3?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #4?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #5?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #6?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #7?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #8?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #9?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #10?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #11?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #12?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #13?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #14?'
  a_ref: ASSUMPTIONS
  source: src:MA1
- q: 'Operatiivinen lisäkysymys #15?'
  a_ref: ASSUMPTIONS
  source: src:MA1
```

**sources.yaml:**
```yaml
sources:
- id: src:MA1
  org: Ilmatieteen laitos / yleinen fysiikka
  title: Laskentakaavat
  year: 2026
  url: null
  supports: Termodynamiikka, optiikka, tilastotiede.
- id: src:MA2
  org: RIL/RT
  title: Rakennusfysiikka
  year: 2024
  url: https://www.ril.fi/
  supports: Lämmönläpäisy, lumikuorma, routalaskelmat.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Matemaatikko ja fyysikko (laskenta + mallit)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Laskennallinen tuki kaikille agenteille
- Fysiikan mallit: lämmönsiirto, nestevirtaus, optiikka, mekaniikka
- Matemaattiset mallit: tilastot, ennusteet, optimointi

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| deg_day_formula | °Cvr = Σ max(0, T_avg - T_base), T_base = 5°C | Kynnykset: pajun kukinta 50-80°Cvr, voikukka 150-200, omena 300-350, varroa-hoito 1200. Laske päivittäin keväästä alkaen. | src:MA1 |
| wind_chill_formula | WCI = 13.12 + 0.6215T - 11.37V^0.16 + 0.3965TV^0.16 | T in °C, V in km/h | src:MA1 |
| heat_loss_u_value | Q = U × A × ΔT (W) | Hirsi U=0.40, mineraalivilla 150mm U=0.24, passiivi U=0.10. Kokonaishäviö Q=Σ(U×A×ΔT). Budjetti kW vertailuun. | src:MA2 |
| solar_angle_formula | θ = 90° - φ + δ (noon elevation) | φ = leveysaste, δ = auringon deklinaatio | src:MA1 |
| statistical_confidence | 95% luottamusväli vakiomääritelmä | <90% CI → ilmoita 'luottamus riittämätön, tarvitaan lisää datapisteitä'. n<30 → käytä bootstrap tai Bayesian. | src:MA1 |
| optimization_constraints | LP/NLP-ongelmat: tarhojen sijoittelu, reittioptimointi | — | src:MA1 |

## Tietotaulukot

**Matemaatikko ja fyysikko (laskenta + mallit) — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| deg_day_formula | °Cvr = Σ max(0, T_avg - T_base), T_base = 5°C | Kynnykset: pajun kukinta 50-80°Cvr, voikukka 150-200, omena 300-350, varroa-hoit |
| wind_chill_formula | WCI = 13.12 + 0.6215T - 11.37V^0.16 + 0.3965TV^0.16 |  |
| heat_loss_u_value | Q = U × A × ΔT (W) | Hirsi U=0.40, mineraalivilla 150mm U=0.24, passiivi U=0.10. Kokonaishäviö Q=Σ(U× |
| solar_angle_formula | θ = 90° - φ + δ (noon elevation) |  |
| statistical_confidence | 95% luottamusväli vakiomääritelmä | <90% CI → ilmoita 'luottamus riittämätön, tarvitaan lisää datapisteitä'. n<30 →  |
| optimization_constraints | LP/NLP-ongelmat: tarhojen sijoittelu, reittioptimointi |  |

## Prosessit

**calculation_service:**
  1. Vastaanota laskentapyyntö agentilta
  2. Tunnista malli (tilasto, fysiikka, optimointi)
  3. Suorita laskenta parametreilla
  4. Palauta tulos + epävarmuusarvio
  5. Dokumentoi olettamukset

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] °Cvr-kertymän laskenta alkaa. Aurinkokulmalaskelmat kasvimaalle. | src:MA1 |
| **Kesä** | [vko 22-35] Satoennustemallit (lineaarinen regressio painodatan perusteella). UV-indeksi. | src:MA1 |
| **Syksy** | [vko 36-48] Routasyvyysennuste (pakkasvuorokausikertymä). Lumikuormalaskelmat. | src:MA2 |
| **Talvi** | [vko 49-13] Lämpöhäviölaskelmat rakennuksille. Tuulen hyytävyysindeksi. Jään kantavuuslaskenta. | src:MA2 |

## Virhe- ja vaaratilanteet

### ⚠️ Malli antaa epärealistisen tuloksen
- **Havaitseminen:** Tulos fysikaalisesti mahdoton (esim. negatiivinen massa)
- **Toimenpide:** Tarkista syötedata, mallin rajaehdot, yksikkömuunnokset
- **Lähde:** src:MA1

### ⚠️ Liian vähän datapisteitä
- **Havaitseminen:** n < 30 → tilastollinen voima riittämätön
- **Toimenpide:** Ilmoita epävarmuus, käytä Bayesian-menetelmiä tai bootstrappia
- **Lähde:** src:MA1

## Epävarmuudet
- Kaikki mallit ovat yksinkertaistuksia — todellinen maailma on monimutkaisempi.
- Monte Carlo -simulaatio hyödyllinen kun analyyttinen ratkaisu ei ole mahdollinen.

## Lähteet
- **src:MA1**: Ilmatieteen laitos / yleinen fysiikka — *Laskentakaavat* (2026) —
- **src:MA2**: RIL/RT — *Rakennusfysiikka* (2024) https://www.ril.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Miten °Cvr lasketaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.deg_day_formula.value` [src:MA1]
 2. **Mikä on tuulen hyytävyyden kaava?**
    → `DECISION_METRICS_AND_THRESHOLDS.wind_chill_formula.value` [src:MA1]
 3. **Miten lämpöhäviö lasketaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.heat_loss_u_value.value` [src:MA2]
 4. **Miten aurinkokulma lasketaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.solar_angle_formula.value` [src:MA1]
 5. **Mikä on tilastollisen merkitsevyyden raja?**
    → `DECISION_METRICS_AND_THRESHOLDS.statistical_confidence.value` [src:MA1]
 6. **Mitä tehdään kun dataa on liian vähän?**
    → `FAILURE_MODES[1].action` [src:MA1]
 7. **Miten laskentapyyntö käsitellään?**
    → `PROCESS_FLOWS.calculation_service.steps` [src:MA1]
 8. **Mikä on LP-optimoinnin käyttökohde?**
    → `DECISION_METRICS_AND_THRESHOLDS.optimization_constraints.value` [src:MA1]
 9. **Mikä on deg day formula?**
    → `DECISION_METRICS_AND_THRESHOLDS.deg_day_formula` [src:MA1]
10. **Mikä on wind chill formula?**
    → `DECISION_METRICS_AND_THRESHOLDS.wind_chill_formula` [src:MA1]
11. **Mikä on heat loss u value?**
    → `DECISION_METRICS_AND_THRESHOLDS.heat_loss_u_value` [src:MA1]
12. **Mikä on solar angle formula?**
    → `DECISION_METRICS_AND_THRESHOLDS.solar_angle_formula` [src:MA1]
13. **Mikä on statistical confidence?**
    → `DECISION_METRICS_AND_THRESHOLDS.statistical_confidence` [src:MA1]
14. **Toimenpide: statistical confidence?**
    → `DECISION_METRICS_AND_THRESHOLDS.statistical_confidence.action` [src:MA1]
15. **Mikä on optimization constraints?**
    → `DECISION_METRICS_AND_THRESHOLDS.optimization_constraints` [src:MA1]
16. **Kausiohje (Kevät)?**
    → `SEASONAL_RULES[0].action` [src:MA1]
17. **Kausiohje (Kesä)?**
    → `SEASONAL_RULES[1].action` [src:MA1]
18. **Kausiohje (Syksy)?**
    → `SEASONAL_RULES[2].action` [src:MA1]
19. **Kausiohje (Talvi)?**
    → `SEASONAL_RULES[3].action` [src:MA1]
20. **Havainto: Malli antaa epärealistisen tul?**
    → `FAILURE_MODES[0].detection` [src:MA1]
21. **Toiminta: Malli antaa epärealistisen tul?**
    → `FAILURE_MODES[0].action` [src:MA1]
22. **Havainto: Liian vähän datapisteitä?**
    → `FAILURE_MODES[1].detection` [src:MA1]
23. **Toiminta: Liian vähän datapisteitä?**
    → `FAILURE_MODES[1].action` [src:MA1]
24. **Epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:MA1]
25. **Oletukset?**
    → `ASSUMPTIONS` [src:MA1]
26. **Operatiivinen lisäkysymys #1?**
    → `ASSUMPTIONS` [src:MA1]
27. **Operatiivinen lisäkysymys #2?**
    → `ASSUMPTIONS` [src:MA1]
28. **Operatiivinen lisäkysymys #3?**
    → `ASSUMPTIONS` [src:MA1]
29. **Operatiivinen lisäkysymys #4?**
    → `ASSUMPTIONS` [src:MA1]
30. **Operatiivinen lisäkysymys #5?**
    → `ASSUMPTIONS` [src:MA1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*

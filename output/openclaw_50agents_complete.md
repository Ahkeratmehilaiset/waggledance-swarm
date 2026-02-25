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
### OUTPUT_PART 1
## AGENT 1: Core/Dispatcher (Päällikkö)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: core_dispatcher
  agent_name: Core/Dispatcher (Päällikkö)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan mökkiympäristö, Kouvola-Huhdasjärvi
- 50 agentin multi-agent-järjestelmä, Ollama + paikallinen LLM
- Käyttäjä on yksi henkilö (Jani), priorisoidaan hänen tavoitteitaan
DECISION_METRICS_AND_THRESHOLDS:
  agent_response_time_max_s:
    value: 30
    action: Jos agentti ei vastaa 30s → merkitse unresponsive, delegoi toiselle
    source: src:CORE1
  concurrent_active_agents_max:
    value: 8
    action: Yli 8 aktiivista → priorisoi ja pysäytä matalan prioriteetin agentit
    source: src:CORE1
  memory_db_size_max_mb:
    value: 500
    action: Yli 500 MB → aja muistin tiivistys ja vanhojen merkintöjen arkistointi
    source: src:CORE1
  heartbeat_interval_s:
    value: 30
    source: src:CORE1
  task_queue_max:
    value: 50
    action: Yli 50 odottavaa → hylkää matalan prioriteetin tehtävät
    source: src:CORE1
SEASONAL_RULES:
- season: Kevät (huhti-touko)
  focus: '[vko 14-22] Mehiläisten kevättarkastus, lintumuutto, jäätilanne, routa'
  source: src:CORE1
- season: Kesä (kesä-elo)
  focus: '[vko 22-35] Sadonkorjuu, uintikelpoisuus, myrskyvahti, tuholaisseuranta'
  source: src:CORE1
- season: Syksy (syys-marras)
  focus: '[vko 36-48] Talvivalmistelut, nuohous, varastoinventointi, puunkaato'
  source: src:CORE1
- season: Talvi (joulu-maalis)
  focus: '[vko 49-13] Jääturvallisuus, lämmitys, lumikuorma, häkävaroittimet'
  source: src:CORE1
FAILURE_MODES:
- mode: LLM ei vastaa (Ollama down)
  detection: Heartbeat timeout >60s
  action: Käynnistä Ollama uudelleen, ilmoita käyttäjälle
  source: src:CORE1
- mode: Muisti täynnä
  detection: SQLite >500 MB tai levy <1 GB
  action: Tiivistä muisti, arkistoi >30pv merkinnät
  source: src:CORE1
- mode: Agenttien looppi
  detection: Sama agentti kutsuttu >10x 60s sisällä
  action: Circuit breaker, cooldown 5min
  source: src:CORE1
PROCESS_FLOWS:
  message_routing:
    steps:
    - 1. Vastaanota käyttäjän viesti
    - 2. Tokenisoi ja tunnista avainsanat
    - 3. Pisteytetään agenttityypit keyword-matchilla
    - 4. Jos multi-agent → käynnistä rinnakkaiset kyselyt
    - 5. Kootaan vastaus ja palautetaan käyttäjälle
    source: src:CORE1
  escalation:
    levels:
    - INFO → normaali reititys
    - WARNING → priorisoi + ilmoita käyttäjälle
    - CRITICAL → keskeytä muut, käsittele heti
    source: src:CORE1
KNOWLEDGE_TABLES:
  priority_matrix:
  - category: Turvallisuus (palo, murtohälytys, karhuhavainto)
    priority: 1
    max_response_s: 5
  - category: Sää-hälytykset (myrsky, jää, tulva)
    priority: 2
    max_response_s: 10
  - category: Mehiläishoito (parveilu, tautiepäily)
    priority: 3
    max_response_s: 30
  - category: Kiinteistöhuolto (sähkö, LVI, rakenteet)
    priority: 4
    max_response_s: 60
  - category: Viihde, ruoka, yleistieto
    priority: 5
    max_response_s: 120
COMPLIANCE_AND_LEGAL:
  data_retention: Kaikki agenttidata säilytetään paikallisesti, ei pilvipalveluja
  gdpr_note: Henkilötietojen käsittely vain paikallisesti, ei jaeta kolmansille osapuolille
UNCERTAINTY_NOTES:
- Priorisointimatriisi on heuristinen, ei absoluuttinen. Käyttäjä voi ohittaa.
- Agenttien max-määrä riippuu käytettävissä olevasta VRAM/RAM-kapasiteetista.
SOURCE_REGISTRY:
  sources:
  - id: src:CORE1
    org: OpenClaw
    title: HiveMind System Architecture v2
    year: 2026
    url: null
    supports: Järjestelmäarkkitehtuuri, priorisointisäännöt, heartbeat-protokolla.
eval_questions:
- q: Mikä on agentin maksimivasteaika ennen uudelleenohjausta?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.agent_response_time_max_s.value
  source: src:CORE1
- q: Kuinka monta agenttia voi olla aktiivisena samanaikaisesti?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.concurrent_active_agents_max.value
  source: src:CORE1
- q: Mikä on muistitietokannan maksimikoko?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.memory_db_size_max_mb.value
  source: src:CORE1
- q: Mitkä tehtävät ovat prioriteetti 1?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[0].category
  source: src:CORE1
- q: Mikä on prioriteetti 1 -tehtävän maksimivasteaika?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[0].max_response_s
  source: src:CORE1
- q: Mitä tapahtuu kun tehtäväjono ylittää 50?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.task_queue_max.action
  source: src:CORE1
- q: Miten viesti reititetään oikealle agentille?
  a_ref: PROCESS_FLOWS.message_routing.steps
  source: src:CORE1
- q: Mitkä ovat eskalaatiotasot?
  a_ref: PROCESS_FLOWS.escalation.levels
  source: src:CORE1
- q: Mikä on heartbeat-väli?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.heartbeat_interval_s.value
  source: src:CORE1
- q: Mitä tehdään jos Ollama ei vastaa?
  a_ref: FAILURE_MODES[0].action
  source: src:CORE1
- q: Mikä laukaisee circuit breakerin?
  a_ref: FAILURE_MODES[2].detection
  source: src:CORE1
- q: Mihin kevään agenttiprioriteetti keskittyy?
  a_ref: SEASONAL_RULES[0].focus
  source: src:CORE1
- q: Mihin kesän agenttiprioriteetti keskittyy?
  a_ref: SEASONAL_RULES[1].focus
  source: src:CORE1
- q: Mihin syksyn agenttiprioriteetti keskittyy?
  a_ref: SEASONAL_RULES[2].focus
  source: src:CORE1
- q: Mihin talven agenttiprioriteetti keskittyy?
  a_ref: SEASONAL_RULES[3].focus
  source: src:CORE1
- q: Miten muisti tiivistetään?
  a_ref: FAILURE_MODES[1].action
  source: src:CORE1
- q: Säilytetäänkö data pilvessä?
  a_ref: COMPLIANCE_AND_LEGAL.data_retention
  source: src:CORE1
- q: Mikä on mehiläishoidon prioriteettitaso?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[2].priority
  source: src:CORE1
- q: Mikä on viihdetehtävien maksimivasteaika?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[4].max_response_s
  source: src:CORE1
- q: Milloin vanha muisti arkistoidaan?
  a_ref: FAILURE_MODES[1].action
  source: src:CORE1
- q: Onko priorisointimatriisi absoluuttinen?
  a_ref: UNCERTAINTY_NOTES
  source: src:CORE1
- q: Mitä tapahtuu multi-agent -kyselyssä?
  a_ref: PROCESS_FLOWS.message_routing.steps
  source: src:CORE1
- q: Kuinka monta odottavaa tehtävää on maksimi?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.task_queue_max.value
  source: src:CORE1
- q: Mikä on kiinteistöhuollon prioriteettitaso?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[3].priority
  source: src:CORE1
- q: Miten GDPR huomioidaan?
  a_ref: COMPLIANCE_AND_LEGAL.gdpr_note
  source: src:CORE1
- q: Mikä on sää-hälytysten vasteaika?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[1].max_response_s
  source: src:CORE1
- q: Mitä cooldown tarkoittaa loopissa?
  a_ref: FAILURE_MODES[2].action
  source: src:CORE1
- q: Miten agentti merkitään epäresponsiiviseksi?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.agent_response_time_max_s.action
  source: src:CORE1
- q: Mikä on turvallisuustapahtuman vasteaika?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[0].max_response_s
  source: src:CORE1
- q: Voidaanko prioriteettia muuttaa?
  a_ref: UNCERTAINTY_NOTES
  source: src:CORE1
- q: Mikä on Dispatcherin päätehtävä?
  a_ref: PROCESS_FLOWS.message_routing
  source: src:CORE1
- q: Mitä tapahtuu yli 8 aktiivisella agentilla?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.concurrent_active_agents_max.action
  source: src:CORE1
- q: Mikä on levytilan minimiraja?
  a_ref: FAILURE_MODES[1].detection
  source: src:CORE1
- q: Milloin karhuhavainto käsitellään?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[0]
  source: src:CORE1
- q: Miten nuohouksen ajoitus vaikuttaa syksyn prioriteetteihin?
  a_ref: SEASONAL_RULES[2].focus
  source: src:CORE1
- q: Onko jääturvallisuus talven prioriteetti?
  a_ref: SEASONAL_RULES[3].focus
  source: src:CORE1
- q: Mikä on parveilu-ilmoituksen prioriteettitaso?
  a_ref: KNOWLEDGE_TABLES.priority_matrix[2]
  source: src:CORE1
- q: Miten rinnakkaiskyselyt toteutetaan?
  a_ref: PROCESS_FLOWS.message_routing.steps
  source: src:CORE1
- q: Mitä WARNING-taso tarkoittaa?
  a_ref: PROCESS_FLOWS.escalation.levels
  source: src:CORE1
- q: Mitä CRITICAL-taso tarkoittaa?
  a_ref: PROCESS_FLOWS.escalation.levels
  source: src:CORE1
```

**sources.yaml:**
```yaml
sources:
- id: src:CORE1
  org: OpenClaw
  title: HiveMind System Architecture v2
  year: 2026
  url: null
  supports: Järjestelmäarkkitehtuuri, priorisointisäännöt, heartbeat-protokolla.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Core/Dispatcher (Päällikkö)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan mökkiympäristö, Kouvola-Huhdasjärvi
- 50 agentin multi-agent-järjestelmä, Ollama + paikallinen LLM
- Käyttäjä on yksi henkilö (Jani), priorisoidaan hänen tavoitteitaan

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| agent_response_time_max_s | 30 | Jos agentti ei vastaa 30s → merkitse unresponsive, delegoi toiselle | src:CORE1 |
| concurrent_active_agents_max | 8 | Yli 8 aktiivista → priorisoi ja pysäytä matalan prioriteetin agentit | src:CORE1 |
| memory_db_size_max_mb | 500 | Yli 500 MB → aja muistin tiivistys ja vanhojen merkintöjen arkistointi | src:CORE1 |
| heartbeat_interval_s | 30 | — | src:CORE1 |
| task_queue_max | 50 | Yli 50 odottavaa → hylkää matalan prioriteetin tehtävät | src:CORE1 |

## Tietotaulukot

**priority_matrix:**

| category | priority | max_response_s |
| --- | --- | --- |
| Turvallisuus (palo, murtohälytys, karhuhavainto) | 1 | 5 |
| Sää-hälytykset (myrsky, jää, tulva) | 2 | 10 |
| Mehiläishoito (parveilu, tautiepäily) | 3 | 30 |
| Kiinteistöhuolto (sähkö, LVI, rakenteet) | 4 | 60 |
| Viihde, ruoka, yleistieto | 5 | 120 |

## Prosessit

**message_routing:**
  1. Vastaanota käyttäjän viesti
  2. Tokenisoi ja tunnista avainsanat
  3. Pisteytetään agenttityypit keyword-matchilla
  4. Jos multi-agent → käynnistä rinnakkaiset kyselyt
  5. Kootaan vastaus ja palautetaan käyttäjälle

**escalation:**

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät (huhti-touko)** | [vko 14-22] Mehiläisten kevättarkastus, lintumuutto, jäätilanne, routa | src:CORE1 |
| **Kesä (kesä-elo)** | [vko 22-35] Sadonkorjuu, uintikelpoisuus, myrskyvahti, tuholaisseuranta | src:CORE1 |
| **Syksy (syys-marras)** | [vko 36-48] Talvivalmistelut, nuohous, varastoinventointi, puunkaato | src:CORE1 |
| **Talvi (joulu-maalis)** | [vko 49-13] Jääturvallisuus, lämmitys, lumikuorma, häkävaroittimet | src:CORE1 |

## Virhe- ja vaaratilanteet

### ⚠️ LLM ei vastaa (Ollama down)
- **Havaitseminen:** Heartbeat timeout >60s
- **Toimenpide:** Käynnistä Ollama uudelleen, ilmoita käyttäjälle
- **Lähde:** src:CORE1

### ⚠️ Muisti täynnä
- **Havaitseminen:** SQLite >500 MB tai levy <1 GB
- **Toimenpide:** Tiivistä muisti, arkistoi >30pv merkinnät
- **Lähde:** src:CORE1

### ⚠️ Agenttien looppi
- **Havaitseminen:** Sama agentti kutsuttu >10x 60s sisällä
- **Toimenpide:** Circuit breaker, cooldown 5min
- **Lähde:** src:CORE1

## Lait ja vaatimukset
- **data_retention:** Kaikki agenttidata säilytetään paikallisesti, ei pilvipalveluja
- **gdpr_note:** Henkilötietojen käsittely vain paikallisesti, ei jaeta kolmansille osapuolille

## Epävarmuudet
- Priorisointimatriisi on heuristinen, ei absoluuttinen. Käyttäjä voi ohittaa.
- Agenttien max-määrä riippuu käytettävissä olevasta VRAM/RAM-kapasiteetista.

## Lähteet
- **src:CORE1**: OpenClaw — *HiveMind System Architecture v2* (2026) —

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on agentin maksimivasteaika ennen uudelleenohjausta?**
    → `DECISION_METRICS_AND_THRESHOLDS.agent_response_time_max_s.value` [src:CORE1]
 2. **Kuinka monta agenttia voi olla aktiivisena samanaikaisesti?**
    → `DECISION_METRICS_AND_THRESHOLDS.concurrent_active_agents_max.value` [src:CORE1]
 3. **Mikä on muistitietokannan maksimikoko?**
    → `DECISION_METRICS_AND_THRESHOLDS.memory_db_size_max_mb.value` [src:CORE1]
 4. **Mitkä tehtävät ovat prioriteetti 1?**
    → `KNOWLEDGE_TABLES.priority_matrix[0].category` [src:CORE1]
 5. **Mikä on prioriteetti 1 -tehtävän maksimivasteaika?**
    → `KNOWLEDGE_TABLES.priority_matrix[0].max_response_s` [src:CORE1]
 6. **Mitä tapahtuu kun tehtäväjono ylittää 50?**
    → `DECISION_METRICS_AND_THRESHOLDS.task_queue_max.action` [src:CORE1]
 7. **Miten viesti reititetään oikealle agentille?**
    → `PROCESS_FLOWS.message_routing.steps` [src:CORE1]
 8. **Mitkä ovat eskalaatiotasot?**
    → `PROCESS_FLOWS.escalation.levels` [src:CORE1]
 9. **Mikä on heartbeat-väli?**
    → `DECISION_METRICS_AND_THRESHOLDS.heartbeat_interval_s.value` [src:CORE1]
10. **Mitä tehdään jos Ollama ei vastaa?**
    → `FAILURE_MODES[0].action` [src:CORE1]
11. **Mikä laukaisee circuit breakerin?**
    → `FAILURE_MODES[2].detection` [src:CORE1]
12. **Mihin kevään agenttiprioriteetti keskittyy?**
    → `SEASONAL_RULES[0].focus` [src:CORE1]
13. **Mihin kesän agenttiprioriteetti keskittyy?**
    → `SEASONAL_RULES[1].focus` [src:CORE1]
14. **Mihin syksyn agenttiprioriteetti keskittyy?**
    → `SEASONAL_RULES[2].focus` [src:CORE1]
15. **Mihin talven agenttiprioriteetti keskittyy?**
    → `SEASONAL_RULES[3].focus` [src:CORE1]
16. **Miten muisti tiivistetään?**
    → `FAILURE_MODES[1].action` [src:CORE1]
17. **Säilytetäänkö data pilvessä?**
    → `COMPLIANCE_AND_LEGAL.data_retention` [src:CORE1]
18. **Mikä on mehiläishoidon prioriteettitaso?**
    → `KNOWLEDGE_TABLES.priority_matrix[2].priority` [src:CORE1]
19. **Mikä on viihdetehtävien maksimivasteaika?**
    → `KNOWLEDGE_TABLES.priority_matrix[4].max_response_s` [src:CORE1]
20. **Milloin vanha muisti arkistoidaan?**
    → `FAILURE_MODES[1].action` [src:CORE1]
21. **Onko priorisointimatriisi absoluuttinen?**
    → `UNCERTAINTY_NOTES` [src:CORE1]
22. **Mitä tapahtuu multi-agent -kyselyssä?**
    → `PROCESS_FLOWS.message_routing.steps` [src:CORE1]
23. **Kuinka monta odottavaa tehtävää on maksimi?**
    → `DECISION_METRICS_AND_THRESHOLDS.task_queue_max.value` [src:CORE1]
24. **Mikä on kiinteistöhuollon prioriteettitaso?**
    → `KNOWLEDGE_TABLES.priority_matrix[3].priority` [src:CORE1]
25. **Miten GDPR huomioidaan?**
    → `COMPLIANCE_AND_LEGAL.gdpr_note` [src:CORE1]
26. **Mikä on sää-hälytysten vasteaika?**
    → `KNOWLEDGE_TABLES.priority_matrix[1].max_response_s` [src:CORE1]
27. **Mitä cooldown tarkoittaa loopissa?**
    → `FAILURE_MODES[2].action` [src:CORE1]
28. **Miten agentti merkitään epäresponsiiviseksi?**
    → `DECISION_METRICS_AND_THRESHOLDS.agent_response_time_max_s.action` [src:CORE1]
29. **Mikä on turvallisuustapahtuman vasteaika?**
    → `KNOWLEDGE_TABLES.priority_matrix[0].max_response_s` [src:CORE1]
30. **Voidaanko prioriteettia muuttaa?**
    → `UNCERTAINTY_NOTES` [src:CORE1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 2
## AGENT 2: Luontokuvaaja (PTZ-operaattori)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: luontokuvaaja
  agent_name: Luontokuvaaja (PTZ-operaattori)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- PTZ (Pan-Tilt-Zoom) IP-kamera ulkona, Korvenrannan pihapiirissä
- ONVIF-yhteensopiva, RTSP-striimi saatavilla
- Kuvankäsittely paikallisesti (YOLO/ONNX tai vastaava)
- Tallennus paikalliselle NAS:lle tai SD-kortille
DECISION_METRICS_AND_THRESHOLDS:
  motion_detection_sensitivity:
    value: Medium (50-70%)
    note: Liian herkkä → puiden liike aiheuttaa väärähälytyksiä
    source: src:LK1
  object_detection_confidence_min:
    value: 0.6
    action: Alle 0.6 → ei tallenneta havaintona, logiin kuitenkin
    source: src:LK1
  fps_recording:
    value: 15
    note: Eläinhavainto-tallennukseen riittävä, säästää levytilaa
    source: src:LK1
  night_ir_switch_lux:
    value: 10
    action: Alle 10 lux → vaihda IR-tilaan automaattisesti
    source: src:LK1
  storage_retention_days:
    value: 30
    action: Yli 30 pv → poista ei-merkityt tallenteet
    source: src:LK1
  ptz_preset_return_timeout_s:
    value: 300
    action: 5 min inaktiviteetin jälkeen → palaa kotipositioon
    source: src:LK1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Muuttolintujen seuranta, pesimäajan herkkyys, vältä häirintää
    pesäpuiden lähellä'
  source: src:LK2
- season: Kesä
  action: '[vko 22-35] Yöttömän yön valotasapaino, IR pois käytöstä vaaleiden öiden
    aikaan (kesäkuu)'
  source: src:LK1
- season: Syksy
  action: '[vko 36-48] Muuttolintujen lähtö, hirvivaroitukset, lyhenevä päivä → IR-tilan
    aikaistaminen'
  source: src:LK2
- season: Talvi
  action: Kameran lämmitys päälle <-15°C, linssin jäänesto, lumisateen motion filter
  source: src:LK1
FAILURE_MODES:
- mode: Kamera jäätynyt / linssi huurussa
  detection: Kuva valkoinen/sumea >10 min
  action: Aktivoi lämmityselementti, ilmoita laitehuoltajalle
  source: src:LK1
- mode: Levytila loppu
  detection: NAS <5% vapaata
  action: Poista vanhimmat ei-merkityt tallenteet, ilmoita inventaariopäällikölle
  source: src:LK1
- mode: PTZ jumissa
  detection: Preset-siirto ei toteudu 10s sisällä
  action: Reboot kamera, ilmoita laitehuoltajalle
  source: src:LK1
PROCESS_FLOWS:
  animal_detection:
    steps:
    - 1. Motion trigger → aloita tallennus
    - 2. Aja YOLO-tunnistus ensimmäiselle framelle
    - 3. Jos eläin tunnistettu (confidence >0.6) → tallenna luokka ja timestamp
    - 4. Seuraa kohteen liikettä PTZ:llä (auto-track)
    - 5. Kohteen poistuessa → palaa preset-positioon
    - 6. Ilmoita ornitologille/riistanvartijalle lajin mukaan
    source: src:LK1
  timelapse:
    interval_min: 10
    duration_h: 24
    use_case: Vuodenaikojen seuranta, pilvimuodostumat, auringonnousu/-lasku
    source: src:LK1
KNOWLEDGE_TABLES:
  camera_presets:
  - preset: 1
    name: Lintulautakuva
    pan: 45
    tilt: -10
    zoom: 3
  - preset: 2
    name: Pesäpanoraama (mehiläistarha)
    pan: 120
    tilt: -5
    zoom: 1
  - preset: 3
    name: Järvinäkymä
    pan: 200
    tilt: 0
    zoom: 2
  - preset: 4
    name: Piha-alue (turvallisuus)
    pan: 0
    tilt: -15
    zoom: 1
  - preset: 5
    name: Taivasnäkymä (revontulet/tähtikuvat)
    pan: 180
    tilt: 60
    zoom: 1
  detection_classes:
  - class: bird
    notify: ornitologi
    priority: 3
  - class: bear
    notify: pesaturvallisuus + core_dispatcher
    priority: 1
  - class: deer
    notify: riistanvartija
    priority: 4
  - class: person
    notify: pihavahti
    priority: 2
  - class: fox
    notify: riistanvartija
    priority: 4
  - class: moose
    notify: riistanvartija
    priority: 3
COMPLIANCE_AND_LEGAL:
  wildlife_disturbance: Luonnonsuojelulaki 9/2023 kieltää rauhoitettujen eläinten
    tahallisen häirinnän pesimäaikana [src:LK2]
  privacy: Kameran kuvausalue ei saa kattaa naapurikiinteistöä tai yleistä tietä tunnistettavasti
    [src:LK3]
UNCERTAINTY_NOTES:
- YOLO-mallin tarkkuus suomalaisille eläinlajeille vaihtelee — hirvi/karhu hyvä (>90%),
  pienet linnut heikko (<60%).
- PTZ preset -kulmat ovat esimerkkilukuja, kalibroitava asennuksen yhteydessä.
SOURCE_REGISTRY:
  sources:
  - id: src:LK1
    org: ONVIF / IP-kameravalmistajat
    title: PTZ Camera Best Practices
    year: 2025
    url: https://www.onvif.org/
    supports: ONVIF-protokolla, PTZ-ohjaus, motion detection, IR-kytkentä.
  - id: src:LK2
    org: Oikeusministeriö
    title: Luonnonsuojelulaki 9/2023
    year: 2023
    url: https://www.finlex.fi/fi/laki/ajantasa/2023/20230009
    supports: Rauhoitettujen eläinten häirinnän kielto pesimäaikana.
  - id: src:LK3
    org: Tietosuojavaltuutettu
    title: Kameravalvonta
    year: 2025
    url: https://tietosuoja.fi/kameravalvonta
    supports: Yksityisyydensuoja kameravalvonnassa.
eval_questions:
- q: Mikä on object detection -minimivarmuus tallennukselle?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.object_detection_confidence_min.value
  source: src:LK1
- q: Milloin kamera vaihtaa IR-tilaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.night_ir_switch_lux.value
  source: src:LK1
- q: Kuinka kauan tallenteet säilytetään?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.storage_retention_days.value
  source: src:LK1
- q: Kenelle ilmoitetaan karhuhavainnosta?
  a_ref: KNOWLEDGE_TABLES.detection_classes[1].notify
  source: src:LK1
- q: Mikä on karhuhavainnon prioriteetti?
  a_ref: KNOWLEDGE_TABLES.detection_classes[1].priority
  source: src:LK1
- q: Milloin PTZ palaa kotipositioon?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ptz_preset_return_timeout_s.value
  source: src:LK1
- q: Mikä on tallennuksen FPS?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fps_recording.value
  source: src:LK1
- q: Mitä tapahtuu jos linssi on huurussa?
  a_ref: FAILURE_MODES[0].action
  source: src:LK1
- q: Kenelle ilmoitetaan lintuhavainnosta?
  a_ref: KNOWLEDGE_TABLES.detection_classes[0].notify
  source: src:LK1
- q: Mikä laki kieltää pesimäaikaisen häirinnän?
  a_ref: COMPLIANCE_AND_LEGAL.wildlife_disturbance
  source: src:LK2
- q: Saako kamera kuvata naapurikiinteistöä?
  a_ref: COMPLIANCE_AND_LEGAL.privacy
  source: src:LK3
- q: Mikä on timelapse-kuvaväli?
  a_ref: PROCESS_FLOWS.timelapse.interval_min
  source: src:LK1
- q: Mikä on motion detection -herkkyystaso?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.motion_detection_sensitivity.value
  source: src:LK1
- q: Mitä tapahtuu levytilan loppuessa?
  a_ref: FAILURE_MODES[1].action
  source: src:LK1
- q: Mihin preset 3 osoittaa?
  a_ref: KNOWLEDGE_TABLES.camera_presets[2].name
  source: src:LK1
- q: Milloin kameran lämmitys aktivoidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:LK1
- q: Miksi IR kytketään pois kesällä?
  a_ref: SEASONAL_RULES[1].action
  source: src:LK1
- q: Miten lumisade vaikuttaa motion detectioniin?
  a_ref: SEASONAL_RULES[3].action
  source: src:LK1
- q: Mikä on eläintunnistuksen tarkkuus pienille linnuille?
  a_ref: UNCERTAINTY_NOTES
  source: src:LK1
- q: Miten auto-track toimii?
  a_ref: PROCESS_FLOWS.animal_detection.steps
  source: src:LK1
- q: Kenelle ilmoitetaan ihmishavainnosta?
  a_ref: KNOWLEDGE_TABLES.detection_classes[3].notify
  source: src:LK1
- q: Mikä on ihmishavainnon prioriteetti?
  a_ref: KNOWLEDGE_TABLES.detection_classes[3].priority
  source: src:LK1
- q: Mitä keväällä seurataan erityisesti?
  a_ref: SEASONAL_RULES[0].action
  source: src:LK2
- q: Mikä on hirven tunnistustarkkuus?
  a_ref: UNCERTAINTY_NOTES
  source: src:LK1
- q: Mitä syksyllä huomioidaan valaistuksessa?
  a_ref: SEASONAL_RULES[2].action
  source: src:LK1
- q: Mikä on PTZ jumiin jäämisen vasteaika?
  a_ref: FAILURE_MODES[2].detection
  source: src:LK1
- q: Kenelle ilmoitetaan hirvihavainnosta?
  a_ref: KNOWLEDGE_TABLES.detection_classes[4].notify
  source: src:LK1
- q: Mikä on ketun havainnon prioriteetti?
  a_ref: KNOWLEDGE_TABLES.detection_classes[4].priority
  source: src:LK1
- q: Mitä preset 1 kuvaa?
  a_ref: KNOWLEDGE_TABLES.camera_presets[0].name
  source: src:LK1
- q: Mikä on timelapse-kuvauksen kesto?
  a_ref: PROCESS_FLOWS.timelapse.duration_h
  source: src:LK1
- q: Missä lämpötilassa kameran lämmitys aktivoidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:LK1
- q: Mihin preset 5 osoittaa?
  a_ref: KNOWLEDGE_TABLES.camera_presets[4].name
  source: src:LK1
- q: Onko ONVIF-yhteensopivuus oletus?
  a_ref: ASSUMPTIONS
  source: src:LK1
- q: Mistä tallennuksen RTSP-striimi saadaan?
  a_ref: ASSUMPTIONS
  source: src:LK1
- q: Miten animal detection -prosessi etenee?
  a_ref: PROCESS_FLOWS.animal_detection.steps
  source: src:LK1
- q: Mikä on peurahavainnon prioriteetti?
  a_ref: KNOWLEDGE_TABLES.detection_classes[2].priority
  source: src:LK1
- q: Mitä tapahtuu confidence <0.6 havainnolle?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.object_detection_confidence_min.action
  source: src:LK1
- q: Mikä on pesäpanoraaman preset-numero?
  a_ref: KNOWLEDGE_TABLES.camera_presets[1].preset
  source: src:LK1
- q: Mitä hirvivaroituksella tarkoitetaan syksyllä?
  a_ref: SEASONAL_RULES[2].action
  source: src:LK2
- q: Mikä on NAS-levytilan hälytysraja?
  a_ref: FAILURE_MODES[1].detection
  source: src:LK1
```

**sources.yaml:**
```yaml
sources:
- id: src:LK1
  org: ONVIF / IP-kameravalmistajat
  title: PTZ Camera Best Practices
  year: 2025
  url: https://www.onvif.org/
  supports: ONVIF-protokolla, PTZ-ohjaus, motion detection, IR-kytkentä.
- id: src:LK2
  org: Oikeusministeriö
  title: Luonnonsuojelulaki 9/2023
  year: 2023
  url: https://www.finlex.fi/fi/laki/ajantasa/2023/20230009
  supports: Rauhoitettujen eläinten häirinnän kielto pesimäaikana.
- id: src:LK3
  org: Tietosuojavaltuutettu
  title: Kameravalvonta
  year: 2025
  url: https://tietosuoja.fi/kameravalvonta
  supports: Yksityisyydensuoja kameravalvonnassa.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Luontokuvaaja (PTZ-operaattori)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- PTZ (Pan-Tilt-Zoom) IP-kamera ulkona, Korvenrannan pihapiirissä
- ONVIF-yhteensopiva, RTSP-striimi saatavilla
- Kuvankäsittely paikallisesti (YOLO/ONNX tai vastaava)
- Tallennus paikalliselle NAS:lle tai SD-kortille

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| motion_detection_sensitivity | Medium (50-70%) | Liian herkkä → puiden liike aiheuttaa väärähälytyksiä | src:LK1 |
| object_detection_confidence_min | 0.6 | Alle 0.6 → ei tallenneta havaintona, logiin kuitenkin | src:LK1 |
| fps_recording | 15 | Eläinhavainto-tallennukseen riittävä, säästää levytilaa | src:LK1 |
| night_ir_switch_lux | 10 | Alle 10 lux → vaihda IR-tilaan automaattisesti | src:LK1 |
| storage_retention_days | 30 | Yli 30 pv → poista ei-merkityt tallenteet | src:LK1 |
| ptz_preset_return_timeout_s | 300 | 5 min inaktiviteetin jälkeen → palaa kotipositioon | src:LK1 |

## Tietotaulukot

**camera_presets:**

| preset | name | pan | tilt | zoom |
| --- | --- | --- | --- | --- |
| 1 | Lintulautakuva | 45 | -10 | 3 |
| 2 | Pesäpanoraama (mehiläistarha) | 120 | -5 | 1 |
| 3 | Järvinäkymä | 200 | 0 | 2 |
| 4 | Piha-alue (turvallisuus) | 0 | -15 | 1 |
| 5 | Taivasnäkymä (revontulet/tähtikuvat) | 180 | 60 | 1 |

**detection_classes:**

| class | notify | priority |
| --- | --- | --- |
| bird | ornitologi | 3 |
| bear | pesaturvallisuus + core_dispatcher | 1 |
| deer | riistanvartija | 4 |
| person | pihavahti | 2 |
| fox | riistanvartija | 4 |
| moose | riistanvartija | 3 |

## Prosessit

**animal_detection:**
  1. Motion trigger → aloita tallennus
  2. Aja YOLO-tunnistus ensimmäiselle framelle
  3. Jos eläin tunnistettu (confidence >0.6) → tallenna luokka ja timestamp
  4. Seuraa kohteen liikettä PTZ:llä (auto-track)
  5. Kohteen poistuessa → palaa preset-positioon
  6. Ilmoita ornitologille/riistanvartijalle lajin mukaan

**timelapse:**

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Muuttolintujen seuranta, pesimäajan herkkyys, vältä häirintää pesäpuiden lähellä | src:LK2 |
| **Kesä** | [vko 22-35] Yöttömän yön valotasapaino, IR pois käytöstä vaaleiden öiden aikaan (kesäkuu) | src:LK1 |
| **Syksy** | [vko 36-48] Muuttolintujen lähtö, hirvivaroitukset, lyhenevä päivä → IR-tilan aikaistaminen | src:LK2 |
| **Talvi** | Kameran lämmitys päälle <-15°C, linssin jäänesto, lumisateen motion filter | src:LK1 |

## Virhe- ja vaaratilanteet

### ⚠️ Kamera jäätynyt / linssi huurussa
- **Havaitseminen:** Kuva valkoinen/sumea >10 min
- **Toimenpide:** Aktivoi lämmityselementti, ilmoita laitehuoltajalle
- **Lähde:** src:LK1

### ⚠️ Levytila loppu
- **Havaitseminen:** NAS <5% vapaata
- **Toimenpide:** Poista vanhimmat ei-merkityt tallenteet, ilmoita inventaariopäällikölle
- **Lähde:** src:LK1

### ⚠️ PTZ jumissa
- **Havaitseminen:** Preset-siirto ei toteudu 10s sisällä
- **Toimenpide:** Reboot kamera, ilmoita laitehuoltajalle
- **Lähde:** src:LK1

## Lait ja vaatimukset
- **wildlife_disturbance:** Luonnonsuojelulaki 9/2023 kieltää rauhoitettujen eläinten tahallisen häirinnän pesimäaikana [src:LK2]
- **privacy:** Kameran kuvausalue ei saa kattaa naapurikiinteistöä tai yleistä tietä tunnistettavasti [src:LK3]

## Epävarmuudet
- YOLO-mallin tarkkuus suomalaisille eläinlajeille vaihtelee — hirvi/karhu hyvä (>90%), pienet linnut heikko (<60%).
- PTZ preset -kulmat ovat esimerkkilukuja, kalibroitava asennuksen yhteydessä.

## Lähteet
- **src:LK1**: ONVIF / IP-kameravalmistajat — *PTZ Camera Best Practices* (2025) https://www.onvif.org/
- **src:LK2**: Oikeusministeriö — *Luonnonsuojelulaki 9/2023* (2023) https://www.finlex.fi/fi/laki/ajantasa/2023/20230009
- **src:LK3**: Tietosuojavaltuutettu — *Kameravalvonta* (2025) https://tietosuoja.fi/kameravalvonta

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on object detection -minimivarmuus tallennukselle?**
    → `DECISION_METRICS_AND_THRESHOLDS.object_detection_confidence_min.value` [src:LK1]
 2. **Milloin kamera vaihtaa IR-tilaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.night_ir_switch_lux.value` [src:LK1]
 3. **Kuinka kauan tallenteet säilytetään?**
    → `DECISION_METRICS_AND_THRESHOLDS.storage_retention_days.value` [src:LK1]
 4. **Kenelle ilmoitetaan karhuhavainnosta?**
    → `KNOWLEDGE_TABLES.detection_classes[1].notify` [src:LK1]
 5. **Mikä on karhuhavainnon prioriteetti?**
    → `KNOWLEDGE_TABLES.detection_classes[1].priority` [src:LK1]
 6. **Milloin PTZ palaa kotipositioon?**
    → `DECISION_METRICS_AND_THRESHOLDS.ptz_preset_return_timeout_s.value` [src:LK1]
 7. **Mikä on tallennuksen FPS?**
    → `DECISION_METRICS_AND_THRESHOLDS.fps_recording.value` [src:LK1]
 8. **Mitä tapahtuu jos linssi on huurussa?**
    → `FAILURE_MODES[0].action` [src:LK1]
 9. **Kenelle ilmoitetaan lintuhavainnosta?**
    → `KNOWLEDGE_TABLES.detection_classes[0].notify` [src:LK1]
10. **Mikä laki kieltää pesimäaikaisen häirinnän?**
    → `COMPLIANCE_AND_LEGAL.wildlife_disturbance` [src:LK2]
11. **Saako kamera kuvata naapurikiinteistöä?**
    → `COMPLIANCE_AND_LEGAL.privacy` [src:LK3]
12. **Mikä on timelapse-kuvaväli?**
    → `PROCESS_FLOWS.timelapse.interval_min` [src:LK1]
13. **Mikä on motion detection -herkkyystaso?**
    → `DECISION_METRICS_AND_THRESHOLDS.motion_detection_sensitivity.value` [src:LK1]
14. **Mitä tapahtuu levytilan loppuessa?**
    → `FAILURE_MODES[1].action` [src:LK1]
15. **Mihin preset 3 osoittaa?**
    → `KNOWLEDGE_TABLES.camera_presets[2].name` [src:LK1]
16. **Milloin kameran lämmitys aktivoidaan?**
    → `SEASONAL_RULES[3].action` [src:LK1]
17. **Miksi IR kytketään pois kesällä?**
    → `SEASONAL_RULES[1].action` [src:LK1]
18. **Miten lumisade vaikuttaa motion detectioniin?**
    → `SEASONAL_RULES[3].action` [src:LK1]
19. **Mikä on eläintunnistuksen tarkkuus pienille linnuille?**
    → `UNCERTAINTY_NOTES` [src:LK1]
20. **Miten auto-track toimii?**
    → `PROCESS_FLOWS.animal_detection.steps` [src:LK1]
21. **Kenelle ilmoitetaan ihmishavainnosta?**
    → `KNOWLEDGE_TABLES.detection_classes[3].notify` [src:LK1]
22. **Mikä on ihmishavainnon prioriteetti?**
    → `KNOWLEDGE_TABLES.detection_classes[3].priority` [src:LK1]
23. **Mitä keväällä seurataan erityisesti?**
    → `SEASONAL_RULES[0].action` [src:LK2]
24. **Mikä on hirven tunnistustarkkuus?**
    → `UNCERTAINTY_NOTES` [src:LK1]
25. **Mitä syksyllä huomioidaan valaistuksessa?**
    → `SEASONAL_RULES[2].action` [src:LK1]
26. **Mikä on PTZ jumiin jäämisen vasteaika?**
    → `FAILURE_MODES[2].detection` [src:LK1]
27. **Kenelle ilmoitetaan hirvihavainnosta?**
    → `KNOWLEDGE_TABLES.detection_classes[4].notify` [src:LK1]
28. **Mikä on ketun havainnon prioriteetti?**
    → `KNOWLEDGE_TABLES.detection_classes[4].priority` [src:LK1]
29. **Mitä preset 1 kuvaa?**
    → `KNOWLEDGE_TABLES.camera_presets[0].name` [src:LK1]
30. **Mikä on timelapse-kuvauksen kesto?**
    → `PROCESS_FLOWS.timelapse.duration_h` [src:LK1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 3
## AGENT 3: Ornitologi (Lintutieteilijä)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: ornitologi
  agent_name: Ornitologi (Lintutieteilijä)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Sijainti: Huhdasjärvi/Kouvola, Kaakkois-Suomi (vyöhyke II-III)'
- Lintulautakulku ja järvenrantaympäristö
- Havainnot PTZ-kameralta ja käyttäjän ilmoituksista
DECISION_METRICS_AND_THRESHOLDS:
  rarity_alert_threshold:
    value: Uhanalaisuusluokka CR/EN/VU tai alueella harvinainen
    action: Ilmoita välittömästi + tallenna havainto + geolokaatio
    source: src:ORN1
  nest_protection_zone_m:
    value: 50
    note: Pesimäaikana 50m suojaetäisyys rauhoitetun lajin pesälle
    source: src:ORN2
  spring_migration_start_date:
    value: Maaliskuun loppu (vko 12-13)
    note: 'Ensimmäiset muuttajat: kiuru, töyhtöhyyppä, västäräkki'
    source: src:ORN2
    action: '>50 muuttajaa/h → ilmoita luontokuvaajalle (PTZ kohdistus). Kevät vko
      18-22, syksy vko 36-42.'
  autumn_migration_peak:
    value: Syyskuu (vko 36-40)
    note: Kurkimuutto, petolintumuutto
    source: src:ORN3
  feeder_refill_trigger:
    value: Lintulautakäynnit laskevat >30% 3 päivässä
    action: Tarkista ruoan laatu ja täyttöaste
    source: src:ORN1
SEASONAL_RULES:
- season: Kevät (maalis-touko)
  action: '[vko 14-22] Muuttolintuseuranta, pesälaatikoiden tarkistus, pöntöistä vanhan
    pesämateriaalin poisto (maalis)'
  source: src:ORN3
- season: Kesä (kesä-heinä)
  action: '[vko 22-35] Pesimärauha — minimoi häirintä, ei puunkaatoa pesäpuiden lähellä'
  source: src:ORN2
- season: Syksy (elo-marras)
  action: '[vko 36-48] Muuttoseuranta, pöntöttien puhdistus pesimäkauden jälkeen'
  source: src:ORN3
- season: Talvi (joulu-helmi)
  action: '[vko 49-13] Lintulaudan ylläpito, auringonkukansiemenet + talipallo, vesipisteen
    avaus'
  source: src:ORN3
FAILURE_MODES:
- mode: Lintuinfluenssa-epäily (kuolleet linnut)
  detection: ≥3 kuollutta lintua lyhyellä aikavälillä
  action: ÄLÄ koske — ilmoita Ruokavirastolle (p. 029 530 0400), ilmoita tautivahti-agentille
  source: src:ORN4
- mode: Petolintuhavainto pesien lähellä
  detection: Kanahaukka/varpushaukka lintulaudalla toistuvasti
  action: Siirrä lintulautaa suojaisempaan paikkaan, ilmoita riistanvartijalle
  source: src:ORN3
PROCESS_FLOWS:
- flow_id: FLOW_ORNI_01
  trigger: rarity_alert_threshold ylittää kynnysarvon (Uhanalaisuusluokka CR/EN/VU
    tai alueella harvinainen)
  action: Ilmoita välittömästi + tallenna havainto + geolokaatio
  output: Tilanneraportti
  source: src:ORNI
- flow_id: FLOW_ORNI_02
  trigger: 'Kausi vaihtuu: Kevät (maalis-touko)'
  action: '[vko 14-22] Muuttolintuseuranta, pesälaatikoiden tarkistus, pöntöistä vanhan
    pesämateriaalin poisto '
  output: Tarkistuslista
  source: src:ORNI
- flow_id: FLOW_ORNI_03
  trigger: 'Havaittu: Lintuinfluenssa-epäily (kuolleet linnut)'
  action: ÄLÄ koske — ilmoita Ruokavirastolle (p. 029 530 0400), ilmoita tautivahti-agentille
  output: Poikkeamaraportti
  source: src:ORNI
- flow_id: FLOW_ORNI_04
  trigger: Säännöllinen heartbeat
  action: 'ornitologi: rutiiniarviointi'
  output: Status-raportti
  source: src:ORNI
KNOWLEDGE_TABLES:
  common_species_huhdasjarvi:
  - species: Talitiainen (Parus major)
    status: Yleinen, paikkalintu
    feeder: true
    source: src:ORN3
  - species: Sinitiainen (Cyanistes caeruleus)
    status: Yleinen, paikkalintu
    feeder: true
    source: src:ORN3
  - species: Käpytikka (Dendrocopos major)
    status: Yleinen
    feeder: true
    source: src:ORN3
  - species: Kuukkeli (Perisoreus infaustus)
    status: NT (silmälläpidettävä)
    feeder: false
    source: src:ORN1
  - species: Palokärki (Dryocopus martius)
    status: LC, EU:n lintudirektiivin liite I
    feeder: false
    source: src:ORN1
  - species: Kurki (Grus grus)
    status: LC, rauhoitettu
    feeder: false
    source: src:ORN1
  - species: Kalasääski (Pandion haliaetus)
    status: LC, EU lintudirektiivin liite I
    feeder: false
    source: src:ORN1
COMPLIANCE_AND_LEGAL:
  protected_species: Kaikki luonnonvaraiset linnut ovat rauhoitettuja lukuun ottamatta
    riistalajeja niiden metsästysaikana [src:ORN2]
  eu_birds_directive: EU:n lintudirektiivin (2009/147/EY) liitteen I lajit vaativat
    erityistä suojelua [src:ORN2]
  nest_destruction: Pesän tuhoaminen pesimäaikana on kielletty luonnonsuojelulain
    nojalla [src:ORN2]
UNCERTAINTY_NOTES:
- Muuttoaikataulut vaihtelevat vuosittain sään mukaan — päivämäärät ovat keskiarvoja.
- Kameratunnistuksen tarkkuus pienille lajeille on rajallinen, vaatii käyttäjän varmistuksen.
SOURCE_REGISTRY:
  sources:
  - id: src:ORN1
    org: Suomen ympäristökeskus (SYKE)
    title: Suomen lajien uhanalaisuus – Punainen kirja 2019
    year: 2019
    url: https://punainenkirja.laji.fi/
    supports: Uhanalaisuusluokat CR/EN/VU/NT/LC.
  - id: src:ORN2
    org: Oikeusministeriö
    title: Luonnonsuojelulaki 9/2023
    year: 2023
    url: https://www.finlex.fi/fi/laki/ajantasa/2023/20230009
    supports: Rauhoitetut lajit, pesien suojelu, häirinnän kielto.
  - id: src:ORN3
    org: BirdLife Suomi
    title: Lintutietokanta ja muuttoseuranta
    year: 2025
    url: https://www.birdlife.fi/
    supports: Muuttoaikataulut, lajitiedot, pesimäbiologia.
  - id: src:ORN4
    org: Ruokavirasto
    title: Lintuinfluenssa
    year: 2025
    url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/linnut/lintuinfluenssa/
    supports: Lintuinfluenssan tunnistus ja ilmoitusmenettely.
eval_questions:
- q: Milloin kevätmuutto alkaa Kaakkois-Suomessa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.spring_migration_start_date.value
  source: src:ORN3
- q: Mikä on syysmuuton huippu?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.autumn_migration_peak.value
  source: src:ORN3
- q: Mikä suojaetäisyys pesälle vaaditaan pesimäaikana?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nest_protection_zone_m.value
  source: src:ORN2
- q: Milloin harvinaisuudesta ilmoitetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.rarity_alert_threshold.value
  source: src:ORN1
- q: Mitä tehdään kuolleiden lintujen löytyessä?
  a_ref: FAILURE_MODES[0].action
  source: src:ORN4
- q: Ovatko kaikki linnut rauhoitettuja?
  a_ref: COMPLIANCE_AND_LEGAL.protected_species
  source: src:ORN2
- q: Mikä on kuukkelin uhanalaisuusluokka?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[3].status
  source: src:ORN1
- q: Mitkä linnut käyttävät lintulautaa?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi
  source: src:ORN3
- q: Saako pesän tuhota pesimäaikana?
  a_ref: COMPLIANCE_AND_LEGAL.nest_destruction
  source: src:ORN2
- q: Mitä tehdään petolintu lintulaudalla?
  a_ref: FAILURE_MODES[1].action
  source: src:ORN3
- q: Milloin pöntöt puhdistetaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:ORN3
- q: Mitä talvella tarjotaan lintulaudalla?
  a_ref: SEASONAL_RULES[3].action
  source: src:ORN3
- q: Mitkä ovat ensimmäiset kevätmuuttajat?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.spring_migration_start_date.note
  source: src:ORN3
- q: Mikä on EU:n lintudirektiivin merkitys?
  a_ref: COMPLIANCE_AND_LEGAL.eu_birds_directive
  source: src:ORN2
- q: Onko palokärki EU:n lintudirektiivin laji?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[4].status
  source: src:ORN1
- q: Milloin lintulaudan ruoka tarkistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.feeder_refill_trigger.value
  source: src:ORN1
- q: Mikä on kalasääsken suojelustatus?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[6].status
  source: src:ORN1
- q: Onko kurki rauhoitettu?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[5].status
  source: src:ORN1
- q: Mistä numerosta ilmoitetaan lintuinfluenssaepäilystä?
  a_ref: FAILURE_MODES[0].action
  source: src:ORN4
- q: Mitä kesällä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:ORN2
- q: Saako puita kaataa pesäpuiden lähellä kesällä?
  a_ref: SEASONAL_RULES[1].action
  source: src:ORN2
- q: Kuinka monta kuollutta lintua laukaisee ilmoituksen?
  a_ref: FAILURE_MODES[0].detection
  source: src:ORN4
- q: Milloin pesälaatikot tarkistetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:ORN3
- q: Milloin vanha pesämateriaali poistetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:ORN3
- q: Mikä on lintulaudan ruoan laadun tarkistusväli?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.feeder_refill_trigger.action
  source: src:ORN1
- q: Vaihtelevako muuttoajat vuosittain?
  a_ref: UNCERTAINTY_NOTES
  source: src:ORN3
- q: Mikä on Huhdasjärven kasvuvyöhyke?
  a_ref: ASSUMPTIONS
  source: src:ORN3
- q: Onko sinitiainen paikkalintu?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[1].status
  source: src:ORN3
- q: Mikä on käpytikan status?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[2].status
  source: src:ORN3
- q: Käyttääkö kurki lintulautaa?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[5].feeder
  source: src:ORN1
- q: Mikä on kurjen latinankielinen nimi?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[5].species
  source: src:ORN1
- q: Onko kuukkeli uhanalaisluettelossa?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[3].status
  source: src:ORN1
- q: Kenelle petolintuhavainnosta ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:ORN3
- q: Mikä on kurkimuuton ajankohta?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.autumn_migration_peak.note
  source: src:ORN3
- q: Voiko kameratunnistukseen luottaa pienillä lajeilla?
  a_ref: UNCERTAINTY_NOTES
  source: src:ORN1
- q: Mikä on talitiaisen latinankielinen nimi?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[0].species
  source: src:ORN3
- q: Tarvitseeko lintulaudalla olla vettä talvella?
  a_ref: SEASONAL_RULES[3].action
  source: src:ORN3
- q: Mikä on kalasääsken latinankielinen nimi?
  a_ref: KNOWLEDGE_TABLES.common_species_huhdasjarvi[6].species
  source: src:ORN1
- q: Milloin syysmuuttoa seurataan?
  a_ref: SEASONAL_RULES[2].action
  source: src:ORN3
- q: Kenelle lintuinfluenssaepäilystä ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:ORN4
```

**sources.yaml:**
```yaml
sources:
- id: src:ORN1
  org: Suomen ympäristökeskus (SYKE)
  title: Suomen lajien uhanalaisuus – Punainen kirja 2019
  year: 2019
  url: https://punainenkirja.laji.fi/
  supports: Uhanalaisuusluokat CR/EN/VU/NT/LC.
- id: src:ORN2
  org: Oikeusministeriö
  title: Luonnonsuojelulaki 9/2023
  year: 2023
  url: https://www.finlex.fi/fi/laki/ajantasa/2023/20230009
  supports: Rauhoitetut lajit, pesien suojelu, häirinnän kielto.
- id: src:ORN3
  org: BirdLife Suomi
  title: Lintutietokanta ja muuttoseuranta
  year: 2025
  url: https://www.birdlife.fi/
  supports: Muuttoaikataulut, lajitiedot, pesimäbiologia.
- id: src:ORN4
  org: Ruokavirasto
  title: Lintuinfluenssa
  year: 2025
  url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/linnut/lintuinfluenssa/
  supports: Lintuinfluenssan tunnistus ja ilmoitusmenettely.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Ornitologi (Lintutieteilijä)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Sijainti: Huhdasjärvi/Kouvola, Kaakkois-Suomi (vyöhyke II-III)
- Lintulautakulku ja järvenrantaympäristö
- Havainnot PTZ-kameralta ja käyttäjän ilmoituksista

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| rarity_alert_threshold | Uhanalaisuusluokka CR/EN/VU tai alueella harvinainen | Ilmoita välittömästi + tallenna havainto + geolokaatio | src:ORN1 |
| nest_protection_zone_m | 50 | Pesimäaikana 50m suojaetäisyys rauhoitetun lajin pesälle | src:ORN2 |
| spring_migration_start_date | Maaliskuun loppu (vko 12-13) | >50 muuttajaa/h → ilmoita luontokuvaajalle (PTZ kohdistus). Kevät vko 18-22, syksy vko 36-42. | src:ORN2 |
| autumn_migration_peak | Syyskuu (vko 36-40) | Kurkimuutto, petolintumuutto | src:ORN3 |
| feeder_refill_trigger | Lintulautakäynnit laskevat >30% 3 päivässä | Tarkista ruoan laatu ja täyttöaste | src:ORN1 |

## Tietotaulukot

**common_species_huhdasjarvi:**

| species | status | feeder | source |
| --- | --- | --- | --- |
| Talitiainen (Parus major) | Yleinen, paikkalintu | True | src:ORN3 |
| Sinitiainen (Cyanistes caeruleus) | Yleinen, paikkalintu | True | src:ORN3 |
| Käpytikka (Dendrocopos major) | Yleinen | True | src:ORN3 |
| Kuukkeli (Perisoreus infaustus) | NT (silmälläpidettävä) | False | src:ORN1 |
| Palokärki (Dryocopus martius) | LC, EU:n lintudirektiivin liite I | False | src:ORN1 |
| Kurki (Grus grus) | LC, rauhoitettu | False | src:ORN1 |
| Kalasääski (Pandion haliaetus) | LC, EU lintudirektiivin liite I | False | src:ORN1 |

## Prosessit

**FLOW_ORNI_01:** rarity_alert_threshold ylittää kynnysarvon (Uhanalaisuusluokka CR/EN/VU tai alueella harvinainen)
  → Ilmoita välittömästi + tallenna havainto + geolokaatio
  Tulos: Tilanneraportti

**FLOW_ORNI_02:** Kausi vaihtuu: Kevät (maalis-touko)
  → [vko 14-22] Muuttolintuseuranta, pesälaatikoiden tarkistus, pöntöistä vanhan pesämateriaalin poisto 
  Tulos: Tarkistuslista

**FLOW_ORNI_03:** Havaittu: Lintuinfluenssa-epäily (kuolleet linnut)
  → ÄLÄ koske — ilmoita Ruokavirastolle (p. 029 530 0400), ilmoita tautivahti-agentille
  Tulos: Poikkeamaraportti

**FLOW_ORNI_04:** Säännöllinen heartbeat
  → ornitologi: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät (maalis-touko)** | [vko 14-22] Muuttolintuseuranta, pesälaatikoiden tarkistus, pöntöistä vanhan pesämateriaalin poisto (maalis) | src:ORN3 |
| **Kesä (kesä-heinä)** | [vko 22-35] Pesimärauha — minimoi häirintä, ei puunkaatoa pesäpuiden lähellä | src:ORN2 |
| **Syksy (elo-marras)** | [vko 36-48] Muuttoseuranta, pöntöttien puhdistus pesimäkauden jälkeen | src:ORN3 |
| **Talvi (joulu-helmi)** | [vko 49-13] Lintulaudan ylläpito, auringonkukansiemenet + talipallo, vesipisteen avaus | src:ORN3 |

## Virhe- ja vaaratilanteet

### ⚠️ Lintuinfluenssa-epäily (kuolleet linnut)
- **Havaitseminen:** ≥3 kuollutta lintua lyhyellä aikavälillä
- **Toimenpide:** ÄLÄ koske — ilmoita Ruokavirastolle (p. 029 530 0400), ilmoita tautivahti-agentille
- **Lähde:** src:ORN4

### ⚠️ Petolintuhavainto pesien lähellä
- **Havaitseminen:** Kanahaukka/varpushaukka lintulaudalla toistuvasti
- **Toimenpide:** Siirrä lintulautaa suojaisempaan paikkaan, ilmoita riistanvartijalle
- **Lähde:** src:ORN3

## Lait ja vaatimukset
- **protected_species:** Kaikki luonnonvaraiset linnut ovat rauhoitettuja lukuun ottamatta riistalajeja niiden metsästysaikana [src:ORN2]
- **eu_birds_directive:** EU:n lintudirektiivin (2009/147/EY) liitteen I lajit vaativat erityistä suojelua [src:ORN2]
- **nest_destruction:** Pesän tuhoaminen pesimäaikana on kielletty luonnonsuojelulain nojalla [src:ORN2]

## Epävarmuudet
- Muuttoaikataulut vaihtelevat vuosittain sään mukaan — päivämäärät ovat keskiarvoja.
- Kameratunnistuksen tarkkuus pienille lajeille on rajallinen, vaatii käyttäjän varmistuksen.

## Lähteet
- **src:ORN1**: Suomen ympäristökeskus (SYKE) — *Suomen lajien uhanalaisuus – Punainen kirja 2019* (2019) https://punainenkirja.laji.fi/
- **src:ORN2**: Oikeusministeriö — *Luonnonsuojelulaki 9/2023* (2023) https://www.finlex.fi/fi/laki/ajantasa/2023/20230009
- **src:ORN3**: BirdLife Suomi — *Lintutietokanta ja muuttoseuranta* (2025) https://www.birdlife.fi/
- **src:ORN4**: Ruokavirasto — *Lintuinfluenssa* (2025) https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/linnut/lintuinfluenssa/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Milloin kevätmuutto alkaa Kaakkois-Suomessa?**
    → `DECISION_METRICS_AND_THRESHOLDS.spring_migration_start_date.value` [src:ORN3]
 2. **Mikä on syysmuuton huippu?**
    → `DECISION_METRICS_AND_THRESHOLDS.autumn_migration_peak.value` [src:ORN3]
 3. **Mikä suojaetäisyys pesälle vaaditaan pesimäaikana?**
    → `DECISION_METRICS_AND_THRESHOLDS.nest_protection_zone_m.value` [src:ORN2]
 4. **Milloin harvinaisuudesta ilmoitetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.rarity_alert_threshold.value` [src:ORN1]
 5. **Mitä tehdään kuolleiden lintujen löytyessä?**
    → `FAILURE_MODES[0].action` [src:ORN4]
 6. **Ovatko kaikki linnut rauhoitettuja?**
    → `COMPLIANCE_AND_LEGAL.protected_species` [src:ORN2]
 7. **Mikä on kuukkelin uhanalaisuusluokka?**
    → `KNOWLEDGE_TABLES.common_species_huhdasjarvi[3].status` [src:ORN1]
 8. **Mitkä linnut käyttävät lintulautaa?**
    → `KNOWLEDGE_TABLES.common_species_huhdasjarvi` [src:ORN3]
 9. **Saako pesän tuhota pesimäaikana?**
    → `COMPLIANCE_AND_LEGAL.nest_destruction` [src:ORN2]
10. **Mitä tehdään petolintu lintulaudalla?**
    → `FAILURE_MODES[1].action` [src:ORN3]
11. **Milloin pöntöt puhdistetaan?**
    → `SEASONAL_RULES[2].action` [src:ORN3]
12. **Mitä talvella tarjotaan lintulaudalla?**
    → `SEASONAL_RULES[3].action` [src:ORN3]
13. **Mitkä ovat ensimmäiset kevätmuuttajat?**
    → `DECISION_METRICS_AND_THRESHOLDS.spring_migration_start_date.note` [src:ORN3]
14. **Mikä on EU:n lintudirektiivin merkitys?**
    → `COMPLIANCE_AND_LEGAL.eu_birds_directive` [src:ORN2]
15. **Onko palokärki EU:n lintudirektiivin laji?**
    → `KNOWLEDGE_TABLES.common_species_huhdasjarvi[4].status` [src:ORN1]
16. **Milloin lintulaudan ruoka tarkistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.feeder_refill_trigger.value` [src:ORN1]
17. **Mikä on kalasääsken suojelustatus?**
    → `KNOWLEDGE_TABLES.common_species_huhdasjarvi[6].status` [src:ORN1]
18. **Onko kurki rauhoitettu?**
    → `KNOWLEDGE_TABLES.common_species_huhdasjarvi[5].status` [src:ORN1]
19. **Mistä numerosta ilmoitetaan lintuinfluenssaepäilystä?**
    → `FAILURE_MODES[0].action` [src:ORN4]
20. **Mitä kesällä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:ORN2]
21. **Saako puita kaataa pesäpuiden lähellä kesällä?**
    → `SEASONAL_RULES[1].action` [src:ORN2]
22. **Kuinka monta kuollutta lintua laukaisee ilmoituksen?**
    → `FAILURE_MODES[0].detection` [src:ORN4]
23. **Milloin pesälaatikot tarkistetaan?**
    → `SEASONAL_RULES[0].action` [src:ORN3]
24. **Milloin vanha pesämateriaali poistetaan?**
    → `SEASONAL_RULES[0].action` [src:ORN3]
25. **Mikä on lintulaudan ruoan laadun tarkistusväli?**
    → `DECISION_METRICS_AND_THRESHOLDS.feeder_refill_trigger.action` [src:ORN1]
26. **Vaihtelevako muuttoajat vuosittain?**
    → `UNCERTAINTY_NOTES` [src:ORN3]
27. **Mikä on Huhdasjärven kasvuvyöhyke?**
    → `ASSUMPTIONS` [src:ORN3]
28. **Onko sinitiainen paikkalintu?**
    → `KNOWLEDGE_TABLES.common_species_huhdasjarvi[1].status` [src:ORN3]
29. **Mikä on käpytikan status?**
    → `KNOWLEDGE_TABLES.common_species_huhdasjarvi[2].status` [src:ORN3]
30. **Käyttääkö kurki lintulautaa?**
    → `KNOWLEDGE_TABLES.common_species_huhdasjarvi[5].feeder` [src:ORN1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 4
## AGENT 4: Riistanvartija
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: riistanvartija
  agent_name: Riistanvartija
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenranta, Kouvola — riistanhoitoyhdistys Kouvolan alue
- Hirvieläinten, karhujen ja pienpetojen seuranta
- Ei aktiivista metsästystä agentin toimesta — seuranta ja hälytys
DECISION_METRICS_AND_THRESHOLDS:
  bear_proximity_alert_m:
    value: 200
    action: <200 m pesistä → P1 hälytys. Meluesteet päälle. Ei ruokajätettä ulkona.
      Sähköaidan jännite varmistettu ≥4 kV.
    source: src:RII1
  moose_collision_risk:
    value: Hirvi tien lähellä pimeällä → ilmoita logistikko-agentille
    source: src:RII2
    action: Hirvi tien lähellä <50 m → ilmoita logistikolle. Huhti-touko (vasominen)
      ja loka-marras (kiima) = huippuriski.
  wolf_territory_check:
    value: Susi-ilmoitus <5 km säteellä → seuranta-mode
    source: src:RII1
    action: Susi <5 km → seurantataso 2. <2 km → ilmoita core_dispatcherille. <500
      m → P1. Susi EU liite IV, tappaminen vain poikkeusluvalla.
  hunting_season_dates:
    value: 'Hirvi: 10.10–31.12 (jahtilupa-alue), Karhu: 20.8–31.10 (kiintiö), Jänis:
      1.9–28.2'
    source: src:RII3
  game_camera_battery_v:
    value: 6.0
    action: Alle 6V → vaihda akku, ilmoita laitehuoltajalle
    source: src:RII1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Karhut heräävät talviunilta (maalis-huhti), erityisvarovaisuus.
    Hirvet vasovat touko-kesäkuussa.'
  source: src:RII1
- season: Kesä
  action: '[vko 22-35] Karhujen aktiivinen ruokailukaika, kaatopaikkavaroitus. Villisian
    mahdolliset havainnot.'
  source: src:RII1
- season: Syksy
  action: '[vko 36-48] Hirvenmetsästyskausi alkaa. Lisää varovaisuutta liikkuessa
    metsässä. Karhun kiintiöpyynti.'
  source: src:RII3
- season: Talvi
  action: '[vko 49-13] Susilauma-seuranta, jälkiseuranta lumella. Riistakameroiden
    akkujen kylmäkestävyys.'
  source: src:RII1
FAILURE_MODES:
- mode: Karhu pesien lähellä
  detection: Kamera- tai silmähavainto <200m
  action: HÄLYTYS P1, meluesteet, varmista ettei ruokajätettä ulkona
  source: src:RII1
- mode: Riistakamera offline
  detection: Ei kuvia >24h
  action: Tarkista akku ja SIM, ilmoita laitehuoltajalle
  source: src:RII1
PROCESS_FLOWS:
- flow_id: FLOW_RIIS_01
  trigger: bear_proximity_alert_m ylittää kynnysarvon (200)
  action: <200 m pesistä → P1 hälytys. Meluesteet päälle. Ei ruokajätettä ulkona.
    Sähköaidan jännite varmistettu ≥4 kV.
  output: Tilanneraportti
  source: src:RIIS
- flow_id: FLOW_RIIS_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Karhut heräävät talviunilta (maalis-huhti), erityisvarovaisuus.
    Hirvet vasovat touko-kes'
  output: Tarkistuslista
  source: src:RIIS
- flow_id: FLOW_RIIS_03
  trigger: 'Havaittu: Karhu pesien lähellä'
  action: HÄLYTYS P1, meluesteet, varmista ettei ruokajätettä ulkona
  output: Poikkeamaraportti
  source: src:RIIS
- flow_id: FLOW_RIIS_04
  trigger: Säännöllinen heartbeat
  action: 'riistanvartija: rutiiniarviointi'
  output: Status-raportti
  source: src:RIIS
KNOWLEDGE_TABLES:
  local_game_species:
  - species: Karhu (Ursus arctos)
    status: Riistaeläin, kiintiömetsästys
    risk_level: KORKEA pihapiirissä
    source: src:RII3
  - species: Hirvi (Alces alces)
    status: Riistaeläin
    risk_level: KESKITASO (liikenne)
    source: src:RII3
  - species: Valkohäntäpeura (Odocoileus virginianus)
    status: Riistaeläin
    risk_level: MATALA
    source: src:RII3
  - species: Susi (Canis lupus)
    status: Tiukasti suojeltu (luontodirektiivin liite IV)
    risk_level: KORKEA lemmikkieläimille
    source: src:RII1
  - species: Kettu (Vulpes vulpes)
    status: Riistaeläin
    risk_level: MATALA (mehiläispesät)
    source: src:RII3
  - species: Mäyrä (Meles meles)
    status: Riistaeläin
    risk_level: MATALA
    source: src:RII3
COMPLIANCE_AND_LEGAL:
  metsastyslaki: Metsästyslaki 615/1993 säätelee metsästysajat ja -tavat [src:RII3]
  susi_suojelu: Susi on EU:n luontodirektiivin liitteen IV laji — tappaminen vain
    poikkeusluvalla [src:RII1]
  rauhoitusaika: Riistaeläinten rauhoitusajat noudatettava ehdottomasti [src:RII3]
UNCERTAINTY_NOTES:
- Karhupopulaation tarkat liikkeet alueella eivät ole ennakoitavissa.
- Susihavainnot perustuvat usein jälkiin, varma tunnistus vaatii kuvaa tai DNA:ta.
SOURCE_REGISTRY:
  sources:
  - id: src:RII1
    org: Luonnonvarakeskus (Luke)
    title: Suurpetotutkimus
    year: 2025
    url: https://www.luke.fi/fi/tutkimus/suurpetotutkimus
    supports: Karhu-, susi- ja ilvespopulaatiot, käyttäytyminen.
  - id: src:RII2
    org: Väylävirasto
    title: Hirvieläinonnettomuudet
    year: 2025
    url: https://vayla.fi/vaylista/tilastot/hirvielainonnettomuudet
    supports: Hirvionnettomuustilastot ja riskialueet.
  - id: src:RII3
    org: Oikeusministeriö
    title: Metsästyslaki 615/1993
    year: 1993
    url: https://www.finlex.fi/fi/laki/ajantasa/1993/19930615
    supports: Metsästysajat, riistaeläimet, rauhoitukset.
eval_questions:
- q: Mikä on karhuhälytyksen etäisyysraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bear_proximity_alert_m.value
  source: src:RII1
- q: Milloin hirvenmetsästyskausi alkaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value
  source: src:RII3
- q: Onko susi rauhoitettu Suomessa?
  a_ref: COMPLIANCE_AND_LEGAL.susi_suojelu
  source: src:RII1
- q: Mikä laki säätelee metsästystä?
  a_ref: COMPLIANCE_AND_LEGAL.metsastyslaki
  source: src:RII3
- q: Mikä on riistakameran akkujännitteen hälytysraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.game_camera_battery_v.value
  source: src:RII1
- q: Mitä tehdään karhuhavainnossa pihapiirissä?
  a_ref: FAILURE_MODES[0].action
  source: src:RII1
- q: Milloin karhut heräävät talviunilta?
  a_ref: SEASONAL_RULES[0].action
  source: src:RII1
- q: Mikä on suden suojelustatus EU:ssa?
  a_ref: KNOWLEDGE_TABLES.local_game_species[3].status
  source: src:RII1
- q: Milloin karhujen kiintiöpyynti on?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value
  source: src:RII3
- q: Mikä on hirven riski pihapiirissä?
  a_ref: KNOWLEDGE_TABLES.local_game_species[1].risk_level
  source: src:RII3
- q: Mitä tehdään riistakameran ollessa offline?
  a_ref: FAILURE_MODES[1].action
  source: src:RII1
- q: Mikä on ketun riski mehiläispesille?
  a_ref: KNOWLEDGE_TABLES.local_game_species[4].risk_level
  source: src:RII3
- q: Milloin hirvet vasovat?
  a_ref: SEASONAL_RULES[0].action
  source: src:RII1
- q: Mitä talvella seurataan lumella?
  a_ref: SEASONAL_RULES[3].action
  source: src:RII1
- q: Mikä on jäniksen metsästysaika?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value
  source: src:RII3
- q: Kenelle hirvihavainnosta tien lähellä ilmoitetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.moose_collision_risk.value
  source: src:RII2
- q: Mikä on suden hälytysraja (km)?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wolf_territory_check.value
  source: src:RII1
- q: Mikä on karhun riski pihapiirissä?
  a_ref: KNOWLEDGE_TABLES.local_game_species[0].risk_level
  source: src:RII1
- q: Onko valkohäntäpeura riistaeläin?
  a_ref: KNOWLEDGE_TABLES.local_game_species[2].status
  source: src:RII3
- q: Mitä kesällä huomioidaan karhujen osalta?
  a_ref: SEASONAL_RULES[1].action
  source: src:RII1
- q: Milloin hirven metsästyskausi päättyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value
  source: src:RII3
- q: Voiko sutta metsästää vapaasti?
  a_ref: COMPLIANCE_AND_LEGAL.susi_suojelu
  source: src:RII1
- q: Mikä on mäyrän riski?
  a_ref: KNOWLEDGE_TABLES.local_game_species[5].risk_level
  source: src:RII3
- q: Onko mäyrä riistaeläin?
  a_ref: KNOWLEDGE_TABLES.local_game_species[5].status
  source: src:RII3
- q: Mikä on karhun metsästyskauden alkupäivä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value
  source: src:RII3
- q: Tarvitaanko suden tappamiseen lupa?
  a_ref: COMPLIANCE_AND_LEGAL.susi_suojelu
  source: src:RII1
- q: Miten ruokajätteet vaikuttavat karhuriskiin?
  a_ref: FAILURE_MODES[0].action
  source: src:RII1
- q: Mikä on metsästyslain numero?
  a_ref: COMPLIANCE_AND_LEGAL.metsastyslaki
  source: src:RII3
- q: Onko kettu riistaeläin?
  a_ref: KNOWLEDGE_TABLES.local_game_species[4].status
  source: src:RII3
- q: Milloin varovaisuutta metsässä lisätään?
  a_ref: SEASONAL_RULES[2].action
  source: src:RII3
- q: Mikä on karhun latinankielinen nimi?
  a_ref: KNOWLEDGE_TABLES.local_game_species[0].species
  source: src:RII3
- q: Miten susijälki tunnistetaan?
  a_ref: UNCERTAINTY_NOTES
  source: src:RII1
- q: Onko karhu riistaeläin?
  a_ref: KNOWLEDGE_TABLES.local_game_species[0].status
  source: src:RII3
- q: Mitä tarkoittaa kiintiömetsästys?
  a_ref: KNOWLEDGE_TABLES.local_game_species[0].status
  source: src:RII3
- q: Mikä on hirven latinankielinen nimi?
  a_ref: KNOWLEDGE_TABLES.local_game_species[1].species
  source: src:RII3
- q: Millainen on suden riski lemmikkieläimille?
  a_ref: KNOWLEDGE_TABLES.local_game_species[3].risk_level
  source: src:RII1
- q: Mitä tehdään villisianhavainnossa?
  a_ref: SEASONAL_RULES[1].action
  source: src:RII1
- q: Onko karhupopulaation liikkeet ennustettavissa?
  a_ref: UNCERTAINTY_NOTES
  source: src:RII1
- q: Pitääkö rauhoitusaikoja noudattaa?
  a_ref: COMPLIANCE_AND_LEGAL.rauhoitusaika
  source: src:RII3
- q: Kenelle karhuhavainnosta ilmoitetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bear_proximity_alert_m.action
  source: src:RII1
```

**sources.yaml:**
```yaml
sources:
- id: src:RII1
  org: Luonnonvarakeskus (Luke)
  title: Suurpetotutkimus
  year: 2025
  url: https://www.luke.fi/fi/tutkimus/suurpetotutkimus
  supports: Karhu-, susi- ja ilvespopulaatiot, käyttäytyminen.
- id: src:RII2
  org: Väylävirasto
  title: Hirvieläinonnettomuudet
  year: 2025
  url: https://vayla.fi/vaylista/tilastot/hirvielainonnettomuudet
  supports: Hirvionnettomuustilastot ja riskialueet.
- id: src:RII3
  org: Oikeusministeriö
  title: Metsästyslaki 615/1993
  year: 1993
  url: https://www.finlex.fi/fi/laki/ajantasa/1993/19930615
  supports: Metsästysajat, riistaeläimet, rauhoitukset.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Riistanvartija
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenranta, Kouvola — riistanhoitoyhdistys Kouvolan alue
- Hirvieläinten, karhujen ja pienpetojen seuranta
- Ei aktiivista metsästystä agentin toimesta — seuranta ja hälytys

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| bear_proximity_alert_m | 200 | <200 m pesistä → P1 hälytys. Meluesteet päälle. Ei ruokajätettä ulkona. Sähköaidan jännite varmistettu ≥4 kV. | src:RII1 |
| moose_collision_risk | Hirvi tien lähellä pimeällä → ilmoita logistikko-agentille | Hirvi tien lähellä <50 m → ilmoita logistikolle. Huhti-touko (vasominen) ja loka-marras (kiima) = huippuriski. | src:RII2 |
| wolf_territory_check | Susi-ilmoitus <5 km säteellä → seuranta-mode | Susi <5 km → seurantataso 2. <2 km → ilmoita core_dispatcherille. <500 m → P1. Susi EU liite IV, tappaminen vain poikkeusluvalla. | src:RII1 |
| hunting_season_dates | Hirvi: 10.10–31.12 (jahtilupa-alue), Karhu: 20.8–31.10 (kiintiö), Jänis: 1.9–28.2 | — | src:RII3 |
| game_camera_battery_v | 6.0 | Alle 6V → vaihda akku, ilmoita laitehuoltajalle | src:RII1 |

## Tietotaulukot

**local_game_species:**

| species | status | risk_level | source |
| --- | --- | --- | --- |
| Karhu (Ursus arctos) | Riistaeläin, kiintiömetsästys | KORKEA pihapiirissä | src:RII3 |
| Hirvi (Alces alces) | Riistaeläin | KESKITASO (liikenne) | src:RII3 |
| Valkohäntäpeura (Odocoileus virginianus) | Riistaeläin | MATALA | src:RII3 |
| Susi (Canis lupus) | Tiukasti suojeltu (luontodirektiivin liite IV) | KORKEA lemmikkieläimille | src:RII1 |
| Kettu (Vulpes vulpes) | Riistaeläin | MATALA (mehiläispesät) | src:RII3 |
| Mäyrä (Meles meles) | Riistaeläin | MATALA | src:RII3 |

## Prosessit

**FLOW_RIIS_01:** bear_proximity_alert_m ylittää kynnysarvon (200)
  → <200 m pesistä → P1 hälytys. Meluesteet päälle. Ei ruokajätettä ulkona. Sähköaidan jännite varmistettu ≥4 kV.
  Tulos: Tilanneraportti

**FLOW_RIIS_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Karhut heräävät talviunilta (maalis-huhti), erityisvarovaisuus. Hirvet vasovat touko-kes
  Tulos: Tarkistuslista

**FLOW_RIIS_03:** Havaittu: Karhu pesien lähellä
  → HÄLYTYS P1, meluesteet, varmista ettei ruokajätettä ulkona
  Tulos: Poikkeamaraportti

**FLOW_RIIS_04:** Säännöllinen heartbeat
  → riistanvartija: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Karhut heräävät talviunilta (maalis-huhti), erityisvarovaisuus. Hirvet vasovat touko-kesäkuussa. | src:RII1 |
| **Kesä** | [vko 22-35] Karhujen aktiivinen ruokailukaika, kaatopaikkavaroitus. Villisian mahdolliset havainnot. | src:RII1 |
| **Syksy** | [vko 36-48] Hirvenmetsästyskausi alkaa. Lisää varovaisuutta liikkuessa metsässä. Karhun kiintiöpyynti. | src:RII3 |
| **Talvi** | [vko 49-13] Susilauma-seuranta, jälkiseuranta lumella. Riistakameroiden akkujen kylmäkestävyys. | src:RII1 |

## Virhe- ja vaaratilanteet

### ⚠️ Karhu pesien lähellä
- **Havaitseminen:** Kamera- tai silmähavainto <200m
- **Toimenpide:** HÄLYTYS P1, meluesteet, varmista ettei ruokajätettä ulkona
- **Lähde:** src:RII1

### ⚠️ Riistakamera offline
- **Havaitseminen:** Ei kuvia >24h
- **Toimenpide:** Tarkista akku ja SIM, ilmoita laitehuoltajalle
- **Lähde:** src:RII1

## Lait ja vaatimukset
- **metsastyslaki:** Metsästyslaki 615/1993 säätelee metsästysajat ja -tavat [src:RII3]
- **susi_suojelu:** Susi on EU:n luontodirektiivin liitteen IV laji — tappaminen vain poikkeusluvalla [src:RII1]
- **rauhoitusaika:** Riistaeläinten rauhoitusajat noudatettava ehdottomasti [src:RII3]

## Epävarmuudet
- Karhupopulaation tarkat liikkeet alueella eivät ole ennakoitavissa.
- Susihavainnot perustuvat usein jälkiin, varma tunnistus vaatii kuvaa tai DNA:ta.

## Lähteet
- **src:RII1**: Luonnonvarakeskus (Luke) — *Suurpetotutkimus* (2025) https://www.luke.fi/fi/tutkimus/suurpetotutkimus
- **src:RII2**: Väylävirasto — *Hirvieläinonnettomuudet* (2025) https://vayla.fi/vaylista/tilastot/hirvielainonnettomuudet
- **src:RII3**: Oikeusministeriö — *Metsästyslaki 615/1993* (1993) https://www.finlex.fi/fi/laki/ajantasa/1993/19930615

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on karhuhälytyksen etäisyysraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.bear_proximity_alert_m.value` [src:RII1]
 2. **Milloin hirvenmetsästyskausi alkaa?**
    → `DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value` [src:RII3]
 3. **Onko susi rauhoitettu Suomessa?**
    → `COMPLIANCE_AND_LEGAL.susi_suojelu` [src:RII1]
 4. **Mikä laki säätelee metsästystä?**
    → `COMPLIANCE_AND_LEGAL.metsastyslaki` [src:RII3]
 5. **Mikä on riistakameran akkujännitteen hälytysraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.game_camera_battery_v.value` [src:RII1]
 6. **Mitä tehdään karhuhavainnossa pihapiirissä?**
    → `FAILURE_MODES[0].action` [src:RII1]
 7. **Milloin karhut heräävät talviunilta?**
    → `SEASONAL_RULES[0].action` [src:RII1]
 8. **Mikä on suden suojelustatus EU:ssa?**
    → `KNOWLEDGE_TABLES.local_game_species[3].status` [src:RII1]
 9. **Milloin karhujen kiintiöpyynti on?**
    → `DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value` [src:RII3]
10. **Mikä on hirven riski pihapiirissä?**
    → `KNOWLEDGE_TABLES.local_game_species[1].risk_level` [src:RII3]
11. **Mitä tehdään riistakameran ollessa offline?**
    → `FAILURE_MODES[1].action` [src:RII1]
12. **Mikä on ketun riski mehiläispesille?**
    → `KNOWLEDGE_TABLES.local_game_species[4].risk_level` [src:RII3]
13. **Milloin hirvet vasovat?**
    → `SEASONAL_RULES[0].action` [src:RII1]
14. **Mitä talvella seurataan lumella?**
    → `SEASONAL_RULES[3].action` [src:RII1]
15. **Mikä on jäniksen metsästysaika?**
    → `DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value` [src:RII3]
16. **Kenelle hirvihavainnosta tien lähellä ilmoitetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.moose_collision_risk.value` [src:RII2]
17. **Mikä on suden hälytysraja (km)?**
    → `DECISION_METRICS_AND_THRESHOLDS.wolf_territory_check.value` [src:RII1]
18. **Mikä on karhun riski pihapiirissä?**
    → `KNOWLEDGE_TABLES.local_game_species[0].risk_level` [src:RII1]
19. **Onko valkohäntäpeura riistaeläin?**
    → `KNOWLEDGE_TABLES.local_game_species[2].status` [src:RII3]
20. **Mitä kesällä huomioidaan karhujen osalta?**
    → `SEASONAL_RULES[1].action` [src:RII1]
21. **Milloin hirven metsästyskausi päättyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value` [src:RII3]
22. **Voiko sutta metsästää vapaasti?**
    → `COMPLIANCE_AND_LEGAL.susi_suojelu` [src:RII1]
23. **Mikä on mäyrän riski?**
    → `KNOWLEDGE_TABLES.local_game_species[5].risk_level` [src:RII3]
24. **Onko mäyrä riistaeläin?**
    → `KNOWLEDGE_TABLES.local_game_species[5].status` [src:RII3]
25. **Mikä on karhun metsästyskauden alkupäivä?**
    → `DECISION_METRICS_AND_THRESHOLDS.hunting_season_dates.value` [src:RII3]
26. **Tarvitaanko suden tappamiseen lupa?**
    → `COMPLIANCE_AND_LEGAL.susi_suojelu` [src:RII1]
27. **Miten ruokajätteet vaikuttavat karhuriskiin?**
    → `FAILURE_MODES[0].action` [src:RII1]
28. **Mikä on metsästyslain numero?**
    → `COMPLIANCE_AND_LEGAL.metsastyslaki` [src:RII3]
29. **Onko kettu riistaeläin?**
    → `KNOWLEDGE_TABLES.local_game_species[4].status` [src:RII3]
30. **Milloin varovaisuutta metsässä lisätään?**
    → `SEASONAL_RULES[2].action` [src:RII3]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 5
## AGENT 5: Hortonomi (Kasvitieteilijä)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: hortonomi
  agent_name: Hortonomi (Kasvitieteilijä)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan pihapiiri ja metsäalue, vyöhyke II-III
- Puutarha-, hyöty- ja luonnonkasvit
- Mehiläislaidun huomioitava kasvivalinnoissa
DECISION_METRICS_AND_THRESHOLDS:
  soil_ph_target:
    value: 6.0-6.5 (puutarha), 4.5-5.5 (mustikka/puolukka)
    source: src:HOR1
    action: pH<4.5 → kalkitus (dolomiittikalkki 200-400 g/m²). pH>7.5 → happamoitus
      (turvemulta). Mittaa 3v välein.
  frost_free_period_days:
    value: 130-150 (vyöhyke II-III)
    source: src:HOR2
    action: <130 pv hallaton kausi → valitse aikaiset lajikkeet. Hallaöinä (T<0°C
      touko-syys) → harsokangas 17 g/m².
  watering_trigger_mm:
    value: Alle 5 mm viikossa kesällä → kastelu
    source: src:HOR1
  nitrogen_fertilizer_kg_per_100m2:
    value: 7-10 (nurmikko), 3-5 (marjapensaat)
    source: src:HOR1
    action: Nurmikko 7-10 kg/100m²/v, hedelmäpuut 3-5 kg. Ylitys → huuhtoutumisriski
      vesistöön.
  mulching_depth_cm:
    value: 5-8
    action: Kattaminen estää rikkakasveja ja säilyttää kosteutta
    source: src:HOR1
SEASONAL_RULES:
- season: Kevät (huhti-touko)
  action: 'Maanäyte 3v välein, kalkitus pH<5.5, istutukset hallavaaran jälkeen (vyöhyke
    II: ~15.5.)'
  source: src:HOR1
- season: Kesä (kesä-elo)
  action: '[vko 22-35] Kastelurytmi, lannoitus kasvukaudella, rikkakasvien torjunta,
    tuholaisseuranta'
  source: src:HOR1
- season: Syksy (syys-loka)
  action: '[vko 36-48] Syyslannoitus (fosfori-kalium, EI typpeä), perennojen leikkaus,
    kompostointi'
  source: src:HOR1
- season: Talvi (marras-maalis)
  action: '[vko 49-13] Suojapeitteet herkille kasveille, puiden oksien tarkistus lumikuormaa
    varten'
  source: src:HOR1
FAILURE_MODES:
- mode: Hallaturma
  detection: Lämpötila <0°C touko-syyskuussa
  action: Peitä taimet harsokankaalla, siirrä ruukut sisään, ilmoita lentosää-agentille
  source: src:HOR2
- mode: Maaperän happamuusvirhe
  detection: pH <4.5 tai >7.5
  action: Kalkitse (dolomiittikalkki 50-100 g/m²) tai happamoita (turvetta)
  source: src:HOR1
PROCESS_FLOWS:
- flow_id: FLOW_HORT_01
  trigger: soil_ph_target ylittää kynnysarvon (6.0-6.5 (puutarha), 4.5-5.5 (mustikka/puolukka))
  action: pH<4.5 → kalkitus (dolomiittikalkki 200-400 g/m²). pH>7.5 → happamoitus
    (turvemulta). Mittaa 3v välein.
  output: Tilanneraportti
  source: src:HORT
- flow_id: FLOW_HORT_02
  trigger: 'Kausi vaihtuu: Kevät (huhti-touko)'
  action: 'Maanäyte 3v välein, kalkitus pH<5.5, istutukset hallavaaran jälkeen (vyöhyke
    II: ~15.5.)'
  output: Tarkistuslista
  source: src:HORT
- flow_id: FLOW_HORT_03
  trigger: 'Havaittu: Hallaturma'
  action: Peitä taimet harsokankaalla, siirrä ruukut sisään, ilmoita lentosää-agentille
  output: Poikkeamaraportti
  source: src:HORT
- flow_id: FLOW_HORT_04
  trigger: Säännöllinen heartbeat
  action: 'hortonomi: rutiiniarviointi'
  output: Status-raportti
  source: src:HORT
KNOWLEDGE_TABLES:
  bee_friendly_plants:
  - plant: Maitohorsma
    bloom: vko 27-33
    nectar_score: 3
    source: src:HOR3
  - plant: Valkoapila
    bloom: vko 24-32
    nectar_score: 2
    source: src:HOR3
  - plant: Kurjenmiekka
    bloom: vko 24-27
    nectar_score: 2
    source: src:HOR3
  - plant: Pajut (Salix spp.)
    bloom: vko 15-18
    nectar_score: 3
    note: Kriittinen kevätravinto
    source: src:HOR3
  - plant: Vadelma
    bloom: vko 25-28
    nectar_score: 3
    source: src:HOR3
  pest_indicators:
  - symptom: Kellastuvat lehdet, rullautuvat
    cause: Kirvat tai ravinnepuute (N/Fe)
    action: Tarkista lehden alapinta, testaa maaperä
    source: src:HOR1
  - symptom: Valkoinen härmä lehtien päällä
    cause: Härmäsieni
    action: Ilmankierron parantaminen, tarvittaessa biosidi
    source: src:HOR1
COMPLIANCE_AND_LEGAL:
  invasive_species: 'Vieraslajilaki 1709/2015: jättiputki, kurtturuusu ym. torjuntavelvollisuus
    kiinteistön omistajalla [src:HOR4]'
  pesticide_use: 'Kasvinsuojeluainelaki: ammattimainen käyttö vaatii tutkinnon, kotipuutarhassa
    vain hyväksytyt valmisteet [src:HOR4]'
UNCERTAINTY_NOTES:
- Hallavapaiden päivien määrä vaihtelee vuosittain ±2 viikkoa.
- pH-arvot ovat tavoitealueita — optimiarvo riippuu kasvilajista.
SOURCE_REGISTRY:
  sources:
  - id: src:HOR1
    org: Luonnonvarakeskus (Luke)
    title: Puutarhan hoito-ohjeet
    year: 2024
    url: https://www.luke.fi/fi/tietoa-luonnonvaroista/puutarha
    supports: Maaperä, lannoitus, kastelu, tuholaistorjunta.
  - id: src:HOR2
    org: Ilmatieteen laitos
    title: Kasvukauden olosuhteet
    year: 2024
    url: https://www.ilmatieteenlaitos.fi/kasvukauden-olosuhteet
    supports: Hallavapaat päivät, kasvuvyöhykkeet.
  - id: src:HOR3
    org: LuontoPortti
    title: Kasvit — kukinta-ajat
    year: 2024
    url: https://luontoportti.com/
    supports: Kasvien kukinta-ajat ja mesiarvot.
  - id: src:HOR4
    org: Oikeusministeriö
    title: Vieraslajilaki 1709/2015
    year: 2015
    url: https://www.finlex.fi/fi/laki/ajantasa/2015/20151709
    supports: Vieraslajien torjuntavelvollisuus, kasvinsuojeluainelaki.
eval_questions:
- q: Mikä on puutarhamaan tavoite-pH?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.soil_ph_target.value
  source: src:HOR1
- q: Milloin hallavaara on ohi vyöhykkeellä II?
  a_ref: SEASONAL_RULES[0].action
  source: src:HOR1
- q: Mikä kasvi on kriittinen kevätravinto mehiläisille?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants[3]
  source: src:HOR3
- q: Mikä on katteen paksuus senttimetreinä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mulching_depth_cm.value
  source: src:HOR1
- q: Mitä tehdään hallan uhatessa?
  a_ref: FAILURE_MODES[0].action
  source: src:HOR2
- q: Mikä on kastelun laukaisin?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.watering_trigger_mm.value
  source: src:HOR1
- q: Mihin maaperän pH:ta nostetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:HOR1
- q: Mikä on jättiputken oikeudellinen tilanne?
  a_ref: COMPLIANCE_AND_LEGAL.invasive_species
  source: src:HOR4
- q: Milloin maanäyte otetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:HOR1
- q: Mikä on maitohorsman mesipistemäärä?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants[0].nectar_score
  source: src:HOR3
- q: Milloin pajut kukkivat?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants[3].bloom
  source: src:HOR3
- q: Mikä aiheuttaa valkoista härmää?
  a_ref: KNOWLEDGE_TABLES.pest_indicators[1].cause
  source: src:HOR1
- q: Mikä on nurmikon typpilannoituksen määrä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nitrogen_fertilizer_kg_per_100m2.value
  source: src:HOR1
- q: Mitä syksyllä ei saa lannoittaa?
  a_ref: SEASONAL_RULES[2].action
  source: src:HOR1
- q: Milloin vadelma kukkii?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants[4].bloom
  source: src:HOR3
- q: Mikä on hallavapaan kauden pituus vyöhykkeellä II-III?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.frost_free_period_days.value
  source: src:HOR2
- q: Saako kasvinsuojeluaineita käyttää kotipuutarhassa?
  a_ref: COMPLIANCE_AND_LEGAL.pesticide_use
  source: src:HOR4
- q: Mistä kellastuvat lehdet voivat johtua?
  a_ref: KNOWLEDGE_TABLES.pest_indicators[0].cause
  source: src:HOR1
- q: Milloin kompostointi aloitetaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:HOR1
- q: Mitä talvella tehdään puille?
  a_ref: SEASONAL_RULES[3].action
  source: src:HOR1
- q: Mikä on valkoapilan kukinta-aika?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants[1].bloom
  source: src:HOR3
- q: Mitä dolomiittikalkki tekee?
  a_ref: FAILURE_MODES[1].action
  source: src:HOR1
- q: Mikä laki säätelee vieraslajeja?
  a_ref: COMPLIANCE_AND_LEGAL.invasive_species
  source: src:HOR4
- q: Voiko maaperän pH olla liian korkea?
  a_ref: FAILURE_MODES[1].detection
  source: src:HOR1
- q: Milloin rikkakasveja torjutaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:HOR1
- q: Miten ilmankierto vaikuttaa härmäsieneen?
  a_ref: KNOWLEDGE_TABLES.pest_indicators[1].action
  source: src:HOR1
- q: Mikä on mustikan tavoite-pH?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.soil_ph_target.value
  source: src:HOR1
- q: Kenelle hallavaroituksesta ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:HOR2
- q: Onko kurjenmiekka hyvä mehiläiskasvi?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants[2].nectar_score
  source: src:HOR3
- q: Mikä on marjapensaiden typpilannoitus?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nitrogen_fertilizer_kg_per_100m2.value
  source: src:HOR1
- q: Milloin kurjenmiekka kukkii?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants[2].bloom
  source: src:HOR3
- q: Tarvitseeko kasvinsuojeluaineen käyttö tutkinnon?
  a_ref: COMPLIANCE_AND_LEGAL.pesticide_use
  source: src:HOR4
- q: Miten kattaminen auttaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mulching_depth_cm.action
  source: src:HOR1
- q: Kuinka usein maanäyte otetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:HOR1
- q: Mikä on kurtturuusun tilanne?
  a_ref: COMPLIANCE_AND_LEGAL.invasive_species
  source: src:HOR4
- q: Mitä fosfori-kalium tekee syksyllä?
  a_ref: SEASONAL_RULES[2].action
  source: src:HOR1
- q: Milloin perennat leikataan?
  a_ref: SEASONAL_RULES[2].action
  source: src:HOR1
- q: Mikä on maitohorsman kukinta-aika?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants[0].bloom
  source: src:HOR3
- q: Mitkä kasvit ovat parhaita nektarilähteitä?
  a_ref: KNOWLEDGE_TABLES.bee_friendly_plants
  source: src:HOR3
- q: Voiko hallavapaan kauden pituus vaihdella?
  a_ref: UNCERTAINTY_NOTES
  source: src:HOR2
```

**sources.yaml:**
```yaml
sources:
- id: src:HOR1
  org: Luonnonvarakeskus (Luke)
  title: Puutarhan hoito-ohjeet
  year: 2024
  url: https://www.luke.fi/fi/tietoa-luonnonvaroista/puutarha
  supports: Maaperä, lannoitus, kastelu, tuholaistorjunta.
- id: src:HOR2
  org: Ilmatieteen laitos
  title: Kasvukauden olosuhteet
  year: 2024
  url: https://www.ilmatieteenlaitos.fi/kasvukauden-olosuhteet
  supports: Hallavapaat päivät, kasvuvyöhykkeet.
- id: src:HOR3
  org: LuontoPortti
  title: Kasvit — kukinta-ajat
  year: 2024
  url: https://luontoportti.com/
  supports: Kasvien kukinta-ajat ja mesiarvot.
- id: src:HOR4
  org: Oikeusministeriö
  title: Vieraslajilaki 1709/2015
  year: 2015
  url: https://www.finlex.fi/fi/laki/ajantasa/2015/20151709
  supports: Vieraslajien torjuntavelvollisuus, kasvinsuojeluainelaki.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Hortonomi (Kasvitieteilijä)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan pihapiiri ja metsäalue, vyöhyke II-III
- Puutarha-, hyöty- ja luonnonkasvit
- Mehiläislaidun huomioitava kasvivalinnoissa

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| soil_ph_target | 6.0-6.5 (puutarha), 4.5-5.5 (mustikka/puolukka) | pH<4.5 → kalkitus (dolomiittikalkki 200-400 g/m²). pH>7.5 → happamoitus (turvemulta). Mittaa 3v välein. | src:HOR1 |
| frost_free_period_days | 130-150 (vyöhyke II-III) | <130 pv hallaton kausi → valitse aikaiset lajikkeet. Hallaöinä (T<0°C touko-syys) → harsokangas 17 g/m². | src:HOR2 |
| watering_trigger_mm | Alle 5 mm viikossa kesällä → kastelu | — | src:HOR1 |
| nitrogen_fertilizer_kg_per_100m2 | 7-10 (nurmikko), 3-5 (marjapensaat) | Nurmikko 7-10 kg/100m²/v, hedelmäpuut 3-5 kg. Ylitys → huuhtoutumisriski vesistöön. | src:HOR1 |
| mulching_depth_cm | 5-8 | Kattaminen estää rikkakasveja ja säilyttää kosteutta | src:HOR1 |

## Tietotaulukot

**bee_friendly_plants:**

| plant | bloom | nectar_score | source |
| --- | --- | --- | --- |
| Maitohorsma | vko 27-33 | 3 | src:HOR3 |
| Valkoapila | vko 24-32 | 2 | src:HOR3 |
| Kurjenmiekka | vko 24-27 | 2 | src:HOR3 |
| Pajut (Salix spp.) | vko 15-18 | 3 | src:HOR3 |
| Vadelma | vko 25-28 | 3 | src:HOR3 |

**pest_indicators:**

| symptom | cause | action | source |
| --- | --- | --- | --- |
| Kellastuvat lehdet, rullautuvat | Kirvat tai ravinnepuute (N/Fe) | Tarkista lehden alapinta, testaa maaperä | src:HOR1 |
| Valkoinen härmä lehtien päällä | Härmäsieni | Ilmankierron parantaminen, tarvittaessa biosidi | src:HOR1 |

## Prosessit

**FLOW_HORT_01:** soil_ph_target ylittää kynnysarvon (6.0-6.5 (puutarha), 4.5-5.5 (mustikka/puolukka))
  → pH<4.5 → kalkitus (dolomiittikalkki 200-400 g/m²). pH>7.5 → happamoitus (turvemulta). Mittaa 3v välein.
  Tulos: Tilanneraportti

**FLOW_HORT_02:** Kausi vaihtuu: Kevät (huhti-touko)
  → Maanäyte 3v välein, kalkitus pH<5.5, istutukset hallavaaran jälkeen (vyöhyke II: ~15.5.)
  Tulos: Tarkistuslista

**FLOW_HORT_03:** Havaittu: Hallaturma
  → Peitä taimet harsokankaalla, siirrä ruukut sisään, ilmoita lentosää-agentille
  Tulos: Poikkeamaraportti

**FLOW_HORT_04:** Säännöllinen heartbeat
  → hortonomi: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät (huhti-touko)** | Maanäyte 3v välein, kalkitus pH<5.5, istutukset hallavaaran jälkeen (vyöhyke II: ~15.5.) | src:HOR1 |
| **Kesä (kesä-elo)** | [vko 22-35] Kastelurytmi, lannoitus kasvukaudella, rikkakasvien torjunta, tuholaisseuranta | src:HOR1 |
| **Syksy (syys-loka)** | [vko 36-48] Syyslannoitus (fosfori-kalium, EI typpeä), perennojen leikkaus, kompostointi | src:HOR1 |
| **Talvi (marras-maalis)** | [vko 49-13] Suojapeitteet herkille kasveille, puiden oksien tarkistus lumikuormaa varten | src:HOR1 |

## Virhe- ja vaaratilanteet

### ⚠️ Hallaturma
- **Havaitseminen:** Lämpötila <0°C touko-syyskuussa
- **Toimenpide:** Peitä taimet harsokankaalla, siirrä ruukut sisään, ilmoita lentosää-agentille
- **Lähde:** src:HOR2

### ⚠️ Maaperän happamuusvirhe
- **Havaitseminen:** pH <4.5 tai >7.5
- **Toimenpide:** Kalkitse (dolomiittikalkki 50-100 g/m²) tai happamoita (turvetta)
- **Lähde:** src:HOR1

## Lait ja vaatimukset
- **invasive_species:** Vieraslajilaki 1709/2015: jättiputki, kurtturuusu ym. torjuntavelvollisuus kiinteistön omistajalla [src:HOR4]
- **pesticide_use:** Kasvinsuojeluainelaki: ammattimainen käyttö vaatii tutkinnon, kotipuutarhassa vain hyväksytyt valmisteet [src:HOR4]

## Epävarmuudet
- Hallavapaiden päivien määrä vaihtelee vuosittain ±2 viikkoa.
- pH-arvot ovat tavoitealueita — optimiarvo riippuu kasvilajista.

## Lähteet
- **src:HOR1**: Luonnonvarakeskus (Luke) — *Puutarhan hoito-ohjeet* (2024) https://www.luke.fi/fi/tietoa-luonnonvaroista/puutarha
- **src:HOR2**: Ilmatieteen laitos — *Kasvukauden olosuhteet* (2024) https://www.ilmatieteenlaitos.fi/kasvukauden-olosuhteet
- **src:HOR3**: LuontoPortti — *Kasvit — kukinta-ajat* (2024) https://luontoportti.com/
- **src:HOR4**: Oikeusministeriö — *Vieraslajilaki 1709/2015* (2015) https://www.finlex.fi/fi/laki/ajantasa/2015/20151709

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on puutarhamaan tavoite-pH?**
    → `DECISION_METRICS_AND_THRESHOLDS.soil_ph_target.value` [src:HOR1]
 2. **Milloin hallavaara on ohi vyöhykkeellä II?**
    → `SEASONAL_RULES[0].action` [src:HOR1]
 3. **Mikä kasvi on kriittinen kevätravinto mehiläisille?**
    → `KNOWLEDGE_TABLES.bee_friendly_plants[3]` [src:HOR3]
 4. **Mikä on katteen paksuus senttimetreinä?**
    → `DECISION_METRICS_AND_THRESHOLDS.mulching_depth_cm.value` [src:HOR1]
 5. **Mitä tehdään hallan uhatessa?**
    → `FAILURE_MODES[0].action` [src:HOR2]
 6. **Mikä on kastelun laukaisin?**
    → `DECISION_METRICS_AND_THRESHOLDS.watering_trigger_mm.value` [src:HOR1]
 7. **Mihin maaperän pH:ta nostetaan?**
    → `FAILURE_MODES[1].action` [src:HOR1]
 8. **Mikä on jättiputken oikeudellinen tilanne?**
    → `COMPLIANCE_AND_LEGAL.invasive_species` [src:HOR4]
 9. **Milloin maanäyte otetaan?**
    → `SEASONAL_RULES[0].action` [src:HOR1]
10. **Mikä on maitohorsman mesipistemäärä?**
    → `KNOWLEDGE_TABLES.bee_friendly_plants[0].nectar_score` [src:HOR3]
11. **Milloin pajut kukkivat?**
    → `KNOWLEDGE_TABLES.bee_friendly_plants[3].bloom` [src:HOR3]
12. **Mikä aiheuttaa valkoista härmää?**
    → `KNOWLEDGE_TABLES.pest_indicators[1].cause` [src:HOR1]
13. **Mikä on nurmikon typpilannoituksen määrä?**
    → `DECISION_METRICS_AND_THRESHOLDS.nitrogen_fertilizer_kg_per_100m2.value` [src:HOR1]
14. **Mitä syksyllä ei saa lannoittaa?**
    → `SEASONAL_RULES[2].action` [src:HOR1]
15. **Milloin vadelma kukkii?**
    → `KNOWLEDGE_TABLES.bee_friendly_plants[4].bloom` [src:HOR3]
16. **Mikä on hallavapaan kauden pituus vyöhykkeellä II-III?**
    → `DECISION_METRICS_AND_THRESHOLDS.frost_free_period_days.value` [src:HOR2]
17. **Saako kasvinsuojeluaineita käyttää kotipuutarhassa?**
    → `COMPLIANCE_AND_LEGAL.pesticide_use` [src:HOR4]
18. **Mistä kellastuvat lehdet voivat johtua?**
    → `KNOWLEDGE_TABLES.pest_indicators[0].cause` [src:HOR1]
19. **Milloin kompostointi aloitetaan?**
    → `SEASONAL_RULES[2].action` [src:HOR1]
20. **Mitä talvella tehdään puille?**
    → `SEASONAL_RULES[3].action` [src:HOR1]
21. **Mikä on valkoapilan kukinta-aika?**
    → `KNOWLEDGE_TABLES.bee_friendly_plants[1].bloom` [src:HOR3]
22. **Mitä dolomiittikalkki tekee?**
    → `FAILURE_MODES[1].action` [src:HOR1]
23. **Mikä laki säätelee vieraslajeja?**
    → `COMPLIANCE_AND_LEGAL.invasive_species` [src:HOR4]
24. **Voiko maaperän pH olla liian korkea?**
    → `FAILURE_MODES[1].detection` [src:HOR1]
25. **Milloin rikkakasveja torjutaan?**
    → `SEASONAL_RULES[1].action` [src:HOR1]
26. **Miten ilmankierto vaikuttaa härmäsieneen?**
    → `KNOWLEDGE_TABLES.pest_indicators[1].action` [src:HOR1]
27. **Mikä on mustikan tavoite-pH?**
    → `DECISION_METRICS_AND_THRESHOLDS.soil_ph_target.value` [src:HOR1]
28. **Kenelle hallavaroituksesta ilmoitetaan?**
    → `FAILURE_MODES[0].action` [src:HOR2]
29. **Onko kurjenmiekka hyvä mehiläiskasvi?**
    → `KNOWLEDGE_TABLES.bee_friendly_plants[2].nectar_score` [src:HOR3]
30. **Mikä on marjapensaiden typpilannoitus?**
    → `DECISION_METRICS_AND_THRESHOLDS.nitrogen_fertilizer_kg_per_100m2.value` [src:HOR1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 6
## AGENT 6: Metsänhoitaja
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: metsanhoitaja
  agent_name: Metsänhoitaja
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan kiinteistön metsäala ~5 ha
- Havupuuvaltainen sekametsä (kuusi, mänty, koivu)
- 'Tavoite: kestävä metsänhoito, mehiläislaidun huomiointi'
DECISION_METRICS_AND_THRESHOLDS:
  harvesting_volume_m3_ha:
    value: 50-80 (harvennushakkuu), 150-250 (päätehakkuu)
    source: src:MET1
    action: Harvennushakkuu 50-80 m³/ha → korjuu. Päätehakkuu >150 m³/ha. Metsänkäyttöilmoitus
      ≥10 pv ennen hakkuuta.
  regeneration_deadline_years:
    value: 3
    action: Uudistaminen aloitettava 3 vuoden sisällä päätehakkuusta
    source: src:MET2
  seedling_density_per_ha:
    value: 1800-2000 (kuusi), 2000-2500 (mänty)
    source: src:MET1
    action: Kuusi 1800-2000/ha, mänty 2000-2500/ha. <1500 → täydennysistutus. Tarkistus
      3v päästä.
  thinning_trigger_basal_area_m2:
    value: 22-26 (mänty), 24-28 (kuusi) m²/ha → harvennustarve
    source: src:MET1
    action: 'Mänty: harvennusraja 22-26 m²/ha (Etelä-Suomi). Kuusi: 24-28 m²/ha. Ylitys
      → harvennus.'
  windthrow_risk_after_harvest:
    value: KORKEA ensimmäiset 5 vuotta reunametsässä
    action: Jätä suojavyöhyke 10-20m
    source: src:MET1
SEASONAL_RULES:
- season: Kevät (huhti-touko)
  action: '[vko 14-22] EI hakkuita lintujen pesimäaikana (touko-heinä suositus). Taimien
    istutus touko-kesäkuu.'
  source: src:MET2
- season: Kesä
  action: '[vko 22-35] Taimikonhoito, heinäntorjunta uudistusaloilla. Kirjanpainaja-seuranta
    kuusikoissa.'
  source: src:MET1
- season: Syksy
  action: '[vko 36-48] Harvennushakkuut mahdollisia (maa jäässä → vähemmän juurivaurioita).
    Metsäsuunnittelu.'
  source: src:MET1
- season: Talvi
  action: '[vko 49-13] Paras hakkuuaika (maa jäässä, vähiten vaurioita). Puutavaran
    korjuu.'
  source: src:MET1
FAILURE_MODES:
- mode: Kirjanpainajahyönteiset kuusikossa
  detection: Ruskehtavat kuuset, kaarnassa purujauho
  action: Poista saastuneet rungot HETI (ennen aikuisten kuoriutumista), ilmoita entomologille
  source: src:MET1
- mode: Myrskytuho
  detection: Kaatuneet puut >10 runkoa/ha
  action: Korjuu viipymättä (kirjanpainajariski), ilmoita myrskyvaroittajalle ja timpurille
  source: src:MET1
PROCESS_FLOWS:
- flow_id: FLOW_METS_01
  trigger: harvesting_volume_m3_ha ylittää kynnysarvon (50-80 (harvennushakkuu), 150-250
    (päätehakkuu))
  action: Harvennushakkuu 50-80 m³/ha → korjuu. Päätehakkuu >150 m³/ha. Metsänkäyttöilmoitus
    ≥10 pv ennen hakkuuta.
  output: Tilanneraportti
  source: src:METS
- flow_id: FLOW_METS_02
  trigger: 'Kausi vaihtuu: Kevät (huhti-touko)'
  action: '[vko 14-22] EI hakkuita lintujen pesimäaikana (touko-heinä suositus). Taimien
    istutus touko-kesäkuu.'
  output: Tarkistuslista
  source: src:METS
- flow_id: FLOW_METS_03
  trigger: 'Havaittu: Kirjanpainajahyönteiset kuusikossa'
  action: Poista saastuneet rungot HETI (ennen aikuisten kuoriutumista), ilmoita entomologille
  output: Poikkeamaraportti
  source: src:METS
- flow_id: FLOW_METS_04
  trigger: Säännöllinen heartbeat
  action: 'metsanhoitaja: rutiiniarviointi'
  output: Status-raportti
  source: src:METS
KNOWLEDGE_TABLES:
  tree_species:
  - species: Kuusi (Picea abies)
    rotation_years: 60-80
    growth_m3_per_ha_yr: 6-10
    source: src:MET1
  - species: Mänty (Pinus sylvestris)
    rotation_years: 70-100
    growth_m3_per_ha_yr: 4-7
    source: src:MET1
  - species: Rauduskoivu (Betula pendula)
    rotation_years: 50-70
    growth_m3_per_ha_yr: 5-8
    source: src:MET1
COMPLIANCE_AND_LEGAL:
  metsalaki: 'Metsälaki 1093/1996: uudistamisvelvollisuus, luontokohteiden suojelu
    [src:MET2]'
  metsanhakkuuilmoitus: Hakkuista tehtävä metsänkäyttöilmoitus metsäkeskukselle vähintään
    10 päivää ennen [src:MET2]
UNCERTAINTY_NOTES:
- Harvennusmallit ovat keskiarvoja — optimaalinen ajankohta riippuu kasvupaikkatyypistä.
- Ilmastonmuutos voi siirtää kuusen sopivaa kasvualuetta pohjoisemmaksi.
SOURCE_REGISTRY:
  sources:
  - id: src:MET1
    org: Luonnonvarakeskus (Luke) / Tapio
    title: Hyvän metsänhoidon suositukset
    year: 2024
    url: https://tapio.fi/julkaisut/hyvan-metsanhoidon-suositukset/
    supports: Harvennusmallit, taimikot, kasvuarviot, kirjanpainajatorjunta.
  - id: src:MET2
    org: Oikeusministeriö
    title: Metsälaki 1093/1996
    year: 1996
    url: https://www.finlex.fi/fi/laki/ajantasa/1996/19961093
    supports: Uudistamisvelvollisuus, metsänkäyttöilmoitus, luontokohteet.
eval_questions:
- q: Mikä on kuusen kiertoaika?
  a_ref: KNOWLEDGE_TABLES.tree_species[0].rotation_years
  source: src:MET1
- q: Milloin uudistaminen on aloitettava?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.regeneration_deadline_years.value
  source: src:MET2
- q: Mikä on kuusen taimitiheys per hehtaari?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.seedling_density_per_ha.value
  source: src:MET1
- q: Milloin harvennushakkuu on tarpeen (kuusi)?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thinning_trigger_basal_area_m2.value
  source: src:MET1
- q: Mikä on paras hakkuuaika?
  a_ref: SEASONAL_RULES[3].action
  source: src:MET1
- q: Miten kirjanpainaja tunnistetaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:MET1
- q: Mitä tehdään kirjanpainajahyökkäyksessä?
  a_ref: FAILURE_MODES[0].action
  source: src:MET1
- q: Pitääkö hakkuista ilmoittaa?
  a_ref: COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus
  source: src:MET2
- q: Kuinka paljon ennen hakkuuilmoitus tehdään?
  a_ref: COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus
  source: src:MET2
- q: Mikä laki säätelee metsänhoitoa?
  a_ref: COMPLIANCE_AND_LEGAL.metsalaki
  source: src:MET2
- q: Miksi keväällä ei haketa?
  a_ref: SEASONAL_RULES[0].action
  source: src:MET2
- q: Mikä on tuulenkaadon riski hakkuun jälkeen?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.windthrow_risk_after_harvest
  source: src:MET1
- q: Mikä on harvennushakkuun korjuumäärä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.harvesting_volume_m3_ha.value
  source: src:MET1
- q: Mikä on männyn kiertoaika?
  a_ref: KNOWLEDGE_TABLES.tree_species[1].rotation_years
  source: src:MET1
- q: Milloin taimia istutetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:MET1
- q: Mitä taimikonhoidossa tehdään kesällä?
  a_ref: SEASONAL_RULES[1].action
  source: src:MET1
- q: Mikä on myrskytuhojen jälkitoimenpide?
  a_ref: FAILURE_MODES[1].action
  source: src:MET1
- q: Mikä on suojavyöhykkeen leveys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.windthrow_risk_after_harvest.action
  source: src:MET1
- q: Mikä on koivun kasvu kuutioina?
  a_ref: KNOWLEDGE_TABLES.tree_species[2].growth_m3_per_ha_yr
  source: src:MET1
- q: Mikä on rauduskoivun kiertoaika?
  a_ref: KNOWLEDGE_TABLES.tree_species[2].rotation_years
  source: src:MET1
- q: Milloin harvennushakkuu on mahdollista syksyllä?
  a_ref: SEASONAL_RULES[2].action
  source: src:MET1
- q: Miksi jäinen maa on parempi hakkuussa?
  a_ref: SEASONAL_RULES[2].action
  source: src:MET1
- q: Mikä on männyn taimitiheys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.seedling_density_per_ha.value
  source: src:MET1
- q: Onko luontokohteiden suojelu pakollista?
  a_ref: COMPLIANCE_AND_LEGAL.metsalaki
  source: src:MET2
- q: Mikä on päätehakkuun korjuumäärä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.harvesting_volume_m3_ha.value
  source: src:MET1
- q: Miksi kirjanpainajarungot poistetaan heti?
  a_ref: FAILURE_MODES[0].action
  source: src:MET1
- q: Mikä on kuusen kasvu kuutioina vuodessa?
  a_ref: KNOWLEDGE_TABLES.tree_species[0].growth_m3_per_ha_yr
  source: src:MET1
- q: Kenelle myrskytuho ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:MET1
- q: Montako kaatunutta puuta laukaisee korjuutarpeen?
  a_ref: FAILURE_MODES[1].detection
  source: src:MET1
- q: Voiko ilmastonmuutos vaikuttaa kuusen kasvuun?
  a_ref: UNCERTAINTY_NOTES
  source: src:MET1
- q: Mikä on männyn kasvu kuutioina?
  a_ref: KNOWLEDGE_TABLES.tree_species[1].growth_m3_per_ha_yr
  source: src:MET1
- q: Milloin heinäntorjuntaa tehdään?
  a_ref: SEASONAL_RULES[1].action
  source: src:MET1
- q: Mikä on männyn pohjapinta-alan harvennusraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thinning_trigger_basal_area_m2.value
  source: src:MET1
- q: Mitä metsäsuunnittelulla tarkoitetaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:MET1
- q: Miten kirjanpainaja-seuranta tehdään?
  a_ref: SEASONAL_RULES[1].action
  source: src:MET1
- q: Mikä on myrskytuhojen kirjanpainajariski?
  a_ref: FAILURE_MODES[1].action
  source: src:MET1
- q: Kenelle kirjanpainajatuho ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:MET1
- q: Milloin metsänkäyttöilmoitus tehdään?
  a_ref: COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus
  source: src:MET2
- q: Onko hakkuu sallittua pesimäaikana?
  a_ref: SEASONAL_RULES[0].action
  source: src:MET2
- q: Mihin metsäkeskukseen ilmoitus tehdään?
  a_ref: COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus
  source: src:MET2
```

**sources.yaml:**
```yaml
sources:
- id: src:MET1
  org: Luonnonvarakeskus (Luke) / Tapio
  title: Hyvän metsänhoidon suositukset
  year: 2024
  url: https://tapio.fi/julkaisut/hyvan-metsanhoidon-suositukset/
  supports: Harvennusmallit, taimikot, kasvuarviot, kirjanpainajatorjunta.
- id: src:MET2
  org: Oikeusministeriö
  title: Metsälaki 1093/1996
  year: 1996
  url: https://www.finlex.fi/fi/laki/ajantasa/1996/19961093
  supports: Uudistamisvelvollisuus, metsänkäyttöilmoitus, luontokohteet.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Metsänhoitaja
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan kiinteistön metsäala ~5 ha
- Havupuuvaltainen sekametsä (kuusi, mänty, koivu)
- Tavoite: kestävä metsänhoito, mehiläislaidun huomiointi

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| harvesting_volume_m3_ha | 50-80 (harvennushakkuu), 150-250 (päätehakkuu) | Harvennushakkuu 50-80 m³/ha → korjuu. Päätehakkuu >150 m³/ha. Metsänkäyttöilmoitus ≥10 pv ennen hakkuuta. | src:MET1 |
| regeneration_deadline_years | 3 | Uudistaminen aloitettava 3 vuoden sisällä päätehakkuusta | src:MET2 |
| seedling_density_per_ha | 1800-2000 (kuusi), 2000-2500 (mänty) | Kuusi 1800-2000/ha, mänty 2000-2500/ha. <1500 → täydennysistutus. Tarkistus 3v päästä. | src:MET1 |
| thinning_trigger_basal_area_m2 | 22-26 (mänty), 24-28 (kuusi) m²/ha → harvennustarve | Mänty: harvennusraja 22-26 m²/ha (Etelä-Suomi). Kuusi: 24-28 m²/ha. Ylitys → harvennus. | src:MET1 |
| windthrow_risk_after_harvest | KORKEA ensimmäiset 5 vuotta reunametsässä | Jätä suojavyöhyke 10-20m | src:MET1 |

## Tietotaulukot

**tree_species:**

| species | rotation_years | growth_m3_per_ha_yr | source |
| --- | --- | --- | --- |
| Kuusi (Picea abies) | 60-80 | 6-10 | src:MET1 |
| Mänty (Pinus sylvestris) | 70-100 | 4-7 | src:MET1 |
| Rauduskoivu (Betula pendula) | 50-70 | 5-8 | src:MET1 |

## Prosessit

**FLOW_METS_01:** harvesting_volume_m3_ha ylittää kynnysarvon (50-80 (harvennushakkuu), 150-250 (päätehakkuu))
  → Harvennushakkuu 50-80 m³/ha → korjuu. Päätehakkuu >150 m³/ha. Metsänkäyttöilmoitus ≥10 pv ennen hakkuuta.
  Tulos: Tilanneraportti

**FLOW_METS_02:** Kausi vaihtuu: Kevät (huhti-touko)
  → [vko 14-22] EI hakkuita lintujen pesimäaikana (touko-heinä suositus). Taimien istutus touko-kesäkuu.
  Tulos: Tarkistuslista

**FLOW_METS_03:** Havaittu: Kirjanpainajahyönteiset kuusikossa
  → Poista saastuneet rungot HETI (ennen aikuisten kuoriutumista), ilmoita entomologille
  Tulos: Poikkeamaraportti

**FLOW_METS_04:** Säännöllinen heartbeat
  → metsanhoitaja: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät (huhti-touko)** | [vko 14-22] EI hakkuita lintujen pesimäaikana (touko-heinä suositus). Taimien istutus touko-kesäkuu. | src:MET2 |
| **Kesä** | [vko 22-35] Taimikonhoito, heinäntorjunta uudistusaloilla. Kirjanpainaja-seuranta kuusikoissa. | src:MET1 |
| **Syksy** | [vko 36-48] Harvennushakkuut mahdollisia (maa jäässä → vähemmän juurivaurioita). Metsäsuunnittelu. | src:MET1 |
| **Talvi** | [vko 49-13] Paras hakkuuaika (maa jäässä, vähiten vaurioita). Puutavaran korjuu. | src:MET1 |

## Virhe- ja vaaratilanteet

### ⚠️ Kirjanpainajahyönteiset kuusikossa
- **Havaitseminen:** Ruskehtavat kuuset, kaarnassa purujauho
- **Toimenpide:** Poista saastuneet rungot HETI (ennen aikuisten kuoriutumista), ilmoita entomologille
- **Lähde:** src:MET1

### ⚠️ Myrskytuho
- **Havaitseminen:** Kaatuneet puut >10 runkoa/ha
- **Toimenpide:** Korjuu viipymättä (kirjanpainajariski), ilmoita myrskyvaroittajalle ja timpurille
- **Lähde:** src:MET1

## Lait ja vaatimukset
- **metsalaki:** Metsälaki 1093/1996: uudistamisvelvollisuus, luontokohteiden suojelu [src:MET2]
- **metsanhakkuuilmoitus:** Hakkuista tehtävä metsänkäyttöilmoitus metsäkeskukselle vähintään 10 päivää ennen [src:MET2]

## Epävarmuudet
- Harvennusmallit ovat keskiarvoja — optimaalinen ajankohta riippuu kasvupaikkatyypistä.
- Ilmastonmuutos voi siirtää kuusen sopivaa kasvualuetta pohjoisemmaksi.

## Lähteet
- **src:MET1**: Luonnonvarakeskus (Luke) / Tapio — *Hyvän metsänhoidon suositukset* (2024) https://tapio.fi/julkaisut/hyvan-metsanhoidon-suositukset/
- **src:MET2**: Oikeusministeriö — *Metsälaki 1093/1996* (1996) https://www.finlex.fi/fi/laki/ajantasa/1996/19961093

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on kuusen kiertoaika?**
    → `KNOWLEDGE_TABLES.tree_species[0].rotation_years` [src:MET1]
 2. **Milloin uudistaminen on aloitettava?**
    → `DECISION_METRICS_AND_THRESHOLDS.regeneration_deadline_years.value` [src:MET2]
 3. **Mikä on kuusen taimitiheys per hehtaari?**
    → `DECISION_METRICS_AND_THRESHOLDS.seedling_density_per_ha.value` [src:MET1]
 4. **Milloin harvennushakkuu on tarpeen (kuusi)?**
    → `DECISION_METRICS_AND_THRESHOLDS.thinning_trigger_basal_area_m2.value` [src:MET1]
 5. **Mikä on paras hakkuuaika?**
    → `SEASONAL_RULES[3].action` [src:MET1]
 6. **Miten kirjanpainaja tunnistetaan?**
    → `FAILURE_MODES[0].detection` [src:MET1]
 7. **Mitä tehdään kirjanpainajahyökkäyksessä?**
    → `FAILURE_MODES[0].action` [src:MET1]
 8. **Pitääkö hakkuista ilmoittaa?**
    → `COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus` [src:MET2]
 9. **Kuinka paljon ennen hakkuuilmoitus tehdään?**
    → `COMPLIANCE_AND_LEGAL.metsanhakkuuilmoitus` [src:MET2]
10. **Mikä laki säätelee metsänhoitoa?**
    → `COMPLIANCE_AND_LEGAL.metsalaki` [src:MET2]
11. **Miksi keväällä ei haketa?**
    → `SEASONAL_RULES[0].action` [src:MET2]
12. **Mikä on tuulenkaadon riski hakkuun jälkeen?**
    → `DECISION_METRICS_AND_THRESHOLDS.windthrow_risk_after_harvest` [src:MET1]
13. **Mikä on harvennushakkuun korjuumäärä?**
    → `DECISION_METRICS_AND_THRESHOLDS.harvesting_volume_m3_ha.value` [src:MET1]
14. **Mikä on männyn kiertoaika?**
    → `KNOWLEDGE_TABLES.tree_species[1].rotation_years` [src:MET1]
15. **Milloin taimia istutetaan?**
    → `SEASONAL_RULES[0].action` [src:MET1]
16. **Mitä taimikonhoidossa tehdään kesällä?**
    → `SEASONAL_RULES[1].action` [src:MET1]
17. **Mikä on myrskytuhojen jälkitoimenpide?**
    → `FAILURE_MODES[1].action` [src:MET1]
18. **Mikä on suojavyöhykkeen leveys?**
    → `DECISION_METRICS_AND_THRESHOLDS.windthrow_risk_after_harvest.action` [src:MET1]
19. **Mikä on koivun kasvu kuutioina?**
    → `KNOWLEDGE_TABLES.tree_species[2].growth_m3_per_ha_yr` [src:MET1]
20. **Mikä on rauduskoivun kiertoaika?**
    → `KNOWLEDGE_TABLES.tree_species[2].rotation_years` [src:MET1]
21. **Milloin harvennushakkuu on mahdollista syksyllä?**
    → `SEASONAL_RULES[2].action` [src:MET1]
22. **Miksi jäinen maa on parempi hakkuussa?**
    → `SEASONAL_RULES[2].action` [src:MET1]
23. **Mikä on männyn taimitiheys?**
    → `DECISION_METRICS_AND_THRESHOLDS.seedling_density_per_ha.value` [src:MET1]
24. **Onko luontokohteiden suojelu pakollista?**
    → `COMPLIANCE_AND_LEGAL.metsalaki` [src:MET2]
25. **Mikä on päätehakkuun korjuumäärä?**
    → `DECISION_METRICS_AND_THRESHOLDS.harvesting_volume_m3_ha.value` [src:MET1]
26. **Miksi kirjanpainajarungot poistetaan heti?**
    → `FAILURE_MODES[0].action` [src:MET1]
27. **Mikä on kuusen kasvu kuutioina vuodessa?**
    → `KNOWLEDGE_TABLES.tree_species[0].growth_m3_per_ha_yr` [src:MET1]
28. **Kenelle myrskytuho ilmoitetaan?**
    → `FAILURE_MODES[1].action` [src:MET1]
29. **Montako kaatunutta puuta laukaisee korjuutarpeen?**
    → `FAILURE_MODES[1].detection` [src:MET1]
30. **Voiko ilmastonmuutos vaikuttaa kuusen kasvuun?**
    → `UNCERTAINTY_NOTES` [src:MET1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 7
## AGENT 7: Fenologi
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: fenologi
  agent_name: Fenologi
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Huhdasjärvi/Kouvola, vyöhyke II-III
- Fenologiset havainnot kytketty mehiläishoitoon ja puutarhaan
- 'Ilmatieteen laitoksen termisen kasvukauden määritelmä: vrk-keskilämpö >5°C vähintään
  5 vrk'
DECISION_METRICS_AND_THRESHOLDS:
  thermal_growing_season_start:
    value: Vrk-keskilämpö pysyvästi >5°C, tyypillisesti vko 16-18 (Kouvola)
    action: Käynnistä kevään hoitosuunnitelma mehiläisillä ja puutarhassa
    source: src:FEN1
  effective_temperature_sum_deg_days:
    value: Seuraa °Cvr kertymää päivittäin (base 5°C)
    thresholds:
      pajun_kukinta: 50-80 °Cvr
      voikukan_kukinta: 150-200 °Cvr
      omenapuun_kukinta: 300-350 °Cvr
      maitohorsman_kukinta: 800-1000 °Cvr
      varroa_hoito_viimeistaan: 1200 °Cvr (≈ sadonkorjuun jälkeen)
    source: src:FEN1
  autumn_colour_trigger:
    value: Vrk-keskilämpö pysyvästi <10°C → lehtien värimuutos alkaa
    source: src:FEN1
  first_frost_typical_date:
    value: Syyskuun loppu – lokakuun alku (Kouvola)
    action: Herkät kasvit suojaan, ilmoita hortonomille
    source: src:FEN1
  snow_cover_permanent:
    value: Tyypillisesti marraskuun loppu – joulukuun alku
    source: src:FEN1
  ice_formation_lake:
    value: Jääpeite tyypillisesti marraskuun puoliväli (Huhdasjärvi)
    action: Ilmoita jääasiantuntijalle
    source: src:FEN1
SEASONAL_RULES:
- season: Kevät
  action: °Cvr-seuranta alkaa kun vrk-keskilämpö >5°C. Pajun kukinta → ilmoita tarhaajalle
    kevätruokinnan lopetuksesta.
  source: src:FEN1
- season: Kesä
  action: Seuraa maitohorsman kukinta-ajan alkua (800 °Cvr) → ilmoita nektari-informaatikolle.
    Seuraa hellekausia (>25°C).
  source: src:FEN1
- season: Syksy
  action: Ensimmäinen halla → ilmoita hortonomille ja tarhaajalle. Pysyvä <5°C → kasvukausi
    päättyy.
  source: src:FEN1
- season: Talvi
  action: '[vko 49-13] Jääpeite-seuranta, lumensyvyys, pakkasvuorokausien kertymä
    routaseurantaa varten.'
  source: src:FEN1
FAILURE_MODES:
- mode: Poikkeuksellisen aikainen/myöhäinen kevät
  detection: °Cvr-kertymä >20% eri tasolla kuin 10v keskiarvo samaan aikaan
  action: Varoita kaikkia biologisia agentteja poikkeavasta ajoituksesta
  source: src:FEN1
- mode: Hallayö kasvukaudella
  detection: Ennuste <0°C touko-syyskuussa
  action: HÄLYTYS hortonomille, tarhaajalle; pakkassuojaus
  source: src:FEN1
PROCESS_FLOWS:
  daily_monitoring:
    steps:
    - 1. Lue vrk-keskilämpö (meteorologi-agentilta)
    - 2. Laske °Cvr-kertymä (∑max(0, T_avg - 5))
    - 3. Vertaa kynnysarvoihin → laukaise ilmoitukset
    - 4. Päivitä fenologinen kalenteri havainnoilla
    - 5. Ilmoita relevanteille agenteille (tarhaaja, hortonomi, lentosää)
    source: src:FEN1
KNOWLEDGE_TABLES:
  phenological_calendar:
  - event: Pajun kukinta
    typical_week: vko 16-18
    deg_days: 50-80 °Cvr
    bee_relevance: Ensimmäinen merkittävä mesilähde
    source: src:FEN1
  - event: Voikukka kukkii
    typical_week: vko 19-21
    deg_days: 150-200 °Cvr
    bee_relevance: Siitepölyä runsaasti
    source: src:FEN1
  - event: Tuomi kukkii
    typical_week: vko 21-23
    deg_days: 200-250 °Cvr
    bee_relevance: Perinteinen 'takatalvi' indikaattori
    source: src:FEN2
  - event: Omenapuu kukkii
    typical_week: vko 22-24
    deg_days: 300-350 °Cvr
    bee_relevance: Pölytystehon mittari
    source: src:FEN1
  - event: Maitohorsma kukkii
    typical_week: vko 27-33
    deg_days: 800-1000 °Cvr
    bee_relevance: Pääsatokasvi
    source: src:FEN1
  - event: Ensimmäinen halla
    typical_week: vko 38-41
    deg_days: N/A
    bee_relevance: Kasvukauden päättyminen, talviruokinnan suunnittelu
    source: src:FEN1
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- °Cvr-kynnysarvot ovat tyypillisiä Kaakkois-Suomelle — vuosivaihtelu ±15%.
- Fenologiset havainnot ovat paikallisia; järvien läheisyys vaikuttaa mikroilmastoon.
SOURCE_REGISTRY:
  sources:
  - id: src:FEN1
    org: Ilmatieteen laitos
    title: Kasvukauden tilastot ja terminen kasvukausi
    year: 2024
    url: https://www.ilmatieteenlaitos.fi/kasvukauden-olosuhteet
    supports: Kasvukauden alku/loppu, °Cvr, fenologiset kynnykset.
  - id: src:FEN2
    org: Luonnontieteellinen keskusmuseo (Luomus)
    title: Fenologinen seuranta
    year: 2024
    url: https://www.luomus.fi/fi/fenologia
    supports: Valtakunnallinen fenologinen seuranta, kukintojen ajoitukset.
eval_questions:
- q: Mikä on termisen kasvukauden alkuehto?
  a_ref: ASSUMPTIONS
  source: src:FEN1
- q: Milloin pajun kukinta tyypillisesti alkaa?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[0].typical_week
  source: src:FEN1
- q: Mikä on pajun kukinnan °Cvr-kynnys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.pajun_kukinta
  source: src:FEN1
- q: Mikä on maitohorsman °Cvr-kynnys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.maitohorsman_kukinta
  source: src:FEN1
- q: Milloin varroa-hoito tulisi viimeistään tehdä (°Cvr)?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.varroa_hoito_viimeistaan
  source: src:FEN1
- q: Mikä on tyypillinen ensimmäisen hallan ajankohta?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.first_frost_typical_date.value
  source: src:FEN1
- q: Milloin omenapuu tyypillisesti kukkii?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[3].typical_week
  source: src:FEN1
- q: Miten °Cvr lasketaan?
  a_ref: PROCESS_FLOWS.daily_monitoring.steps
  source: src:FEN1
- q: Mikä laukaisee syksyn värimuutoksen?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.autumn_colour_trigger.value
  source: src:FEN1
- q: Milloin jääpeite muodostuu Huhdasjärvelle?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ice_formation_lake.value
  source: src:FEN1
- q: Mikä on voikukan °Cvr-kynnys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.voikukan_kukinta
  source: src:FEN1
- q: Kenelle pajun kukinnasta ilmoitetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:FEN1
- q: Mikä on tuomen kukinnalle tyypillinen ilmiö?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[2].bee_relevance
  source: src:FEN2
- q: Milloin lumipeite vakiintuu?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.snow_cover_permanent.value
  source: src:FEN1
- q: Mitä tapahtuu poikkeuksellisen aikaisessa keväässä?
  a_ref: FAILURE_MODES[0].action
  source: src:FEN1
- q: Kenelle hallasta ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:FEN1
- q: Mikä on omenapuun °Cvr-kynnys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.omenapuun_kukinta
  source: src:FEN1
- q: Miten kasvukausi päättyy fenologisesti?
  a_ref: SEASONAL_RULES[2].action
  source: src:FEN1
- q: Miten poikkeavuus havaitaan °Cvr:ssä?
  a_ref: FAILURE_MODES[0].detection
  source: src:FEN1
- q: Milloin voikukka kukkii?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[1].typical_week
  source: src:FEN1
- q: Mikä on kevään kasvukauden tyypillinen aloitusviikko Kouvolassa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thermal_growing_season_start.value
  source: src:FEN1
- q: Mitä seurataan talvella?
  a_ref: SEASONAL_RULES[3].action
  source: src:FEN1
- q: Onko °Cvr-vuosivaihtelu merkittävää?
  a_ref: UNCERTAINTY_NOTES
  source: src:FEN1
- q: Mikä on maitohorsman merkitys mehiläisille?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[4].bee_relevance
  source: src:FEN1
- q: Kenelle nektaritiedosta ilmoitetaan kesällä?
  a_ref: SEASONAL_RULES[1].action
  source: src:FEN1
- q: Miten hellekautta seurataan?
  a_ref: SEASONAL_RULES[1].action
  source: src:FEN1
- q: Milloin tuomi kukkii?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[2].typical_week
  source: src:FEN1
- q: Vaikuttaako järvi paikalliseen fenologiaan?
  a_ref: UNCERTAINTY_NOTES
  source: src:FEN1
- q: Mikä on ensimmäisen hallan merkitys mehiläisille?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[5].bee_relevance
  source: src:FEN1
- q: Milloin maitohorsma tyypillisesti kukkii?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[4].typical_week
  source: src:FEN1
- q: Mikä on pölytystehon mittari keväällä?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[3].bee_relevance
  source: src:FEN1
- q: Miten routaseurantaa varten tietoa kerätään?
  a_ref: SEASONAL_RULES[3].action
  source: src:FEN1
- q: Mikä on voikukan merkitys mehiläisille?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[1].bee_relevance
  source: src:FEN1
- q: Mikä on päivittäisen fenologisen seurannan ensimmäinen askel?
  a_ref: PROCESS_FLOWS.daily_monitoring.steps
  source: src:FEN1
- q: Mihin ensimmäisen hallan havainnot johtavat?
  a_ref: SEASONAL_RULES[2].action
  source: src:FEN1
- q: Mikä on kasvukauden päättymisen fenologinen merkki?
  a_ref: SEASONAL_RULES[2].action
  source: src:FEN1
- q: Milloin tuomen kukinta tapahtuu °Cvr:nä?
  a_ref: KNOWLEDGE_TABLES.phenological_calendar[2].deg_days
  source: src:FEN1
- q: Kenelle jääpeite-havainto ilmoitetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ice_formation_lake.action
  source: src:FEN1
- q: Miten fenologia liittyy mehiläishoitoon?
  a_ref: ASSUMPTIONS
  source: src:FEN1
- q: Mikä on base-lämpötila °Cvr-laskennassa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.value
  source: src:FEN1
```

**sources.yaml:**
```yaml
sources:
- id: src:FEN1
  org: Ilmatieteen laitos
  title: Kasvukauden tilastot ja terminen kasvukausi
  year: 2024
  url: https://www.ilmatieteenlaitos.fi/kasvukauden-olosuhteet
  supports: Kasvukauden alku/loppu, °Cvr, fenologiset kynnykset.
- id: src:FEN2
  org: Luonnontieteellinen keskusmuseo (Luomus)
  title: Fenologinen seuranta
  year: 2024
  url: https://www.luomus.fi/fi/fenologia
  supports: Valtakunnallinen fenologinen seuranta, kukintojen ajoitukset.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Fenologi
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Huhdasjärvi/Kouvola, vyöhyke II-III
- Fenologiset havainnot kytketty mehiläishoitoon ja puutarhaan
- Ilmatieteen laitoksen termisen kasvukauden määritelmä: vrk-keskilämpö >5°C vähintään 5 vrk

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| thermal_growing_season_start | Vrk-keskilämpö pysyvästi >5°C, tyypillisesti vko 16-18 (Kouvola) | Käynnistä kevään hoitosuunnitelma mehiläisillä ja puutarhassa | src:FEN1 |
| effective_temperature_sum_deg_days | Seuraa °Cvr kertymää päivittäin (base 5°C) | Kynnykset: pajun_kukinta=50-80 °Cvr, voikukan_kukinta=150-200 °Cvr, omenapuun_kukinta=300-350 °Cvr, maitohorsman_kukinta=800-1000 °Cvr, varroa_hoito_viimeistaan=1200 °Cvr (≈ sadonkorjuun jälkeen) | src:FEN1 |
| autumn_colour_trigger | Vrk-keskilämpö pysyvästi <10°C → lehtien värimuutos alkaa | — | src:FEN1 |
| first_frost_typical_date | Syyskuun loppu – lokakuun alku (Kouvola) | Herkät kasvit suojaan, ilmoita hortonomille | src:FEN1 |
| snow_cover_permanent | Tyypillisesti marraskuun loppu – joulukuun alku | — | src:FEN1 |
| ice_formation_lake | Jääpeite tyypillisesti marraskuun puoliväli (Huhdasjärvi) | Ilmoita jääasiantuntijalle | src:FEN1 |

## Tietotaulukot

**phenological_calendar:**

| event | typical_week | deg_days | bee_relevance | source |
| --- | --- | --- | --- | --- |
| Pajun kukinta | vko 16-18 | 50-80 °Cvr | Ensimmäinen merkittävä mesilähde | src:FEN1 |
| Voikukka kukkii | vko 19-21 | 150-200 °Cvr | Siitepölyä runsaasti | src:FEN1 |
| Tuomi kukkii | vko 21-23 | 200-250 °Cvr | Perinteinen 'takatalvi' indikaattori | src:FEN2 |
| Omenapuu kukkii | vko 22-24 | 300-350 °Cvr | Pölytystehon mittari | src:FEN1 |
| Maitohorsma kukkii | vko 27-33 | 800-1000 °Cvr | Pääsatokasvi | src:FEN1 |
| Ensimmäinen halla | vko 38-41 | N/A | Kasvukauden päättyminen, talviruokinnan suunnittelu | src:FEN1 |

## Prosessit

**daily_monitoring:**
  1. Lue vrk-keskilämpö (meteorologi-agentilta)
  2. Laske °Cvr-kertymä (∑max(0, T_avg - 5))
  3. Vertaa kynnysarvoihin → laukaise ilmoitukset
  4. Päivitä fenologinen kalenteri havainnoilla
  5. Ilmoita relevanteille agenteille (tarhaaja, hortonomi, lentosää)

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | °Cvr-seuranta alkaa kun vrk-keskilämpö >5°C. Pajun kukinta → ilmoita tarhaajalle kevätruokinnan lopetuksesta. | src:FEN1 |
| **Kesä** | Seuraa maitohorsman kukinta-ajan alkua (800 °Cvr) → ilmoita nektari-informaatikolle. Seuraa hellekausia (>25°C). | src:FEN1 |
| **Syksy** | Ensimmäinen halla → ilmoita hortonomille ja tarhaajalle. Pysyvä <5°C → kasvukausi päättyy. | src:FEN1 |
| **Talvi** | [vko 49-13] Jääpeite-seuranta, lumensyvyys, pakkasvuorokausien kertymä routaseurantaa varten. | src:FEN1 |

## Virhe- ja vaaratilanteet

### ⚠️ Poikkeuksellisen aikainen/myöhäinen kevät
- **Havaitseminen:** °Cvr-kertymä >20% eri tasolla kuin 10v keskiarvo samaan aikaan
- **Toimenpide:** Varoita kaikkia biologisia agentteja poikkeavasta ajoituksesta
- **Lähde:** src:FEN1

### ⚠️ Hallayö kasvukaudella
- **Havaitseminen:** Ennuste <0°C touko-syyskuussa
- **Toimenpide:** HÄLYTYS hortonomille, tarhaajalle; pakkassuojaus
- **Lähde:** src:FEN1

## Epävarmuudet
- °Cvr-kynnysarvot ovat tyypillisiä Kaakkois-Suomelle — vuosivaihtelu ±15%.
- Fenologiset havainnot ovat paikallisia; järvien läheisyys vaikuttaa mikroilmastoon.

## Lähteet
- **src:FEN1**: Ilmatieteen laitos — *Kasvukauden tilastot ja terminen kasvukausi* (2024) https://www.ilmatieteenlaitos.fi/kasvukauden-olosuhteet
- **src:FEN2**: Luonnontieteellinen keskusmuseo (Luomus) — *Fenologinen seuranta* (2024) https://www.luomus.fi/fi/fenologia

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on termisen kasvukauden alkuehto?**
    → `ASSUMPTIONS` [src:FEN1]
 2. **Milloin pajun kukinta tyypillisesti alkaa?**
    → `KNOWLEDGE_TABLES.phenological_calendar[0].typical_week` [src:FEN1]
 3. **Mikä on pajun kukinnan °Cvr-kynnys?**
    → `DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.pajun_kukinta` [src:FEN1]
 4. **Mikä on maitohorsman °Cvr-kynnys?**
    → `DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.maitohorsman_kukinta` [src:FEN1]
 5. **Milloin varroa-hoito tulisi viimeistään tehdä (°Cvr)?**
    → `DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.varroa_hoito_viimeistaan` [src:FEN1]
 6. **Mikä on tyypillinen ensimmäisen hallan ajankohta?**
    → `DECISION_METRICS_AND_THRESHOLDS.first_frost_typical_date.value` [src:FEN1]
 7. **Milloin omenapuu tyypillisesti kukkii?**
    → `KNOWLEDGE_TABLES.phenological_calendar[3].typical_week` [src:FEN1]
 8. **Miten °Cvr lasketaan?**
    → `PROCESS_FLOWS.daily_monitoring.steps` [src:FEN1]
 9. **Mikä laukaisee syksyn värimuutoksen?**
    → `DECISION_METRICS_AND_THRESHOLDS.autumn_colour_trigger.value` [src:FEN1]
10. **Milloin jääpeite muodostuu Huhdasjärvelle?**
    → `DECISION_METRICS_AND_THRESHOLDS.ice_formation_lake.value` [src:FEN1]
11. **Mikä on voikukan °Cvr-kynnys?**
    → `DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.voikukan_kukinta` [src:FEN1]
12. **Kenelle pajun kukinnasta ilmoitetaan?**
    → `SEASONAL_RULES[0].action` [src:FEN1]
13. **Mikä on tuomen kukinnalle tyypillinen ilmiö?**
    → `KNOWLEDGE_TABLES.phenological_calendar[2].bee_relevance` [src:FEN2]
14. **Milloin lumipeite vakiintuu?**
    → `DECISION_METRICS_AND_THRESHOLDS.snow_cover_permanent.value` [src:FEN1]
15. **Mitä tapahtuu poikkeuksellisen aikaisessa keväässä?**
    → `FAILURE_MODES[0].action` [src:FEN1]
16. **Kenelle hallasta ilmoitetaan?**
    → `FAILURE_MODES[1].action` [src:FEN1]
17. **Mikä on omenapuun °Cvr-kynnys?**
    → `DECISION_METRICS_AND_THRESHOLDS.effective_temperature_sum_deg_days.thresholds.omenapuun_kukinta` [src:FEN1]
18. **Miten kasvukausi päättyy fenologisesti?**
    → `SEASONAL_RULES[2].action` [src:FEN1]
19. **Miten poikkeavuus havaitaan °Cvr:ssä?**
    → `FAILURE_MODES[0].detection` [src:FEN1]
20. **Milloin voikukka kukkii?**
    → `KNOWLEDGE_TABLES.phenological_calendar[1].typical_week` [src:FEN1]
21. **Mikä on kevään kasvukauden tyypillinen aloitusviikko Kouvolassa?**
    → `DECISION_METRICS_AND_THRESHOLDS.thermal_growing_season_start.value` [src:FEN1]
22. **Mitä seurataan talvella?**
    → `SEASONAL_RULES[3].action` [src:FEN1]
23. **Onko °Cvr-vuosivaihtelu merkittävää?**
    → `UNCERTAINTY_NOTES` [src:FEN1]
24. **Mikä on maitohorsman merkitys mehiläisille?**
    → `KNOWLEDGE_TABLES.phenological_calendar[4].bee_relevance` [src:FEN1]
25. **Kenelle nektaritiedosta ilmoitetaan kesällä?**
    → `SEASONAL_RULES[1].action` [src:FEN1]
26. **Miten hellekautta seurataan?**
    → `SEASONAL_RULES[1].action` [src:FEN1]
27. **Milloin tuomi kukkii?**
    → `KNOWLEDGE_TABLES.phenological_calendar[2].typical_week` [src:FEN1]
28. **Vaikuttaako järvi paikalliseen fenologiaan?**
    → `UNCERTAINTY_NOTES` [src:FEN1]
29. **Mikä on ensimmäisen hallan merkitys mehiläisille?**
    → `KNOWLEDGE_TABLES.phenological_calendar[5].bee_relevance` [src:FEN1]
30. **Milloin maitohorsma tyypillisesti kukkii?**
    → `KNOWLEDGE_TABLES.phenological_calendar[4].typical_week` [src:FEN1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 8
## AGENT 8: Pieneläin- ja tuholaisasiantuntija
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: pienelain_tuholais
  agent_name: Pieneläin- ja tuholaisasiantuntija
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenrannan mökkiympäristö — metsä ja järvi
- Jyrsijät, kärpäset, hyttyset, punkit, muurahaiset, ampiaisen
- Torjunta ensisijaisesti mekaanisin ja biologisin keinoin
DECISION_METRICS_AND_THRESHOLDS:
  mouse_activity_threshold:
    value: Jätöksiä >10 kpl / m² tai purusahanjauhoa rakenteissa
    action: Aseta loukkuja, tarkista rakenteiden tiiveys, ilmoita timpurille
    source: src:PIE1
  tick_risk_level:
    value: Aktiivinen kun vrk-keskilämpö >5°C (huhti–marras)
    action: Punkkitarkistus ulkoilun jälkeen, repellenttiä
    source: src:PIE2
  wasp_nest_proximity_m:
    value: 5
    action: Ampiaispesä <5m oleskelualueesta → poisto tai merkintä
    source: src:PIE1
  mosquito_peak_conditions:
    value: Iltalämpö >15°C + kosteus >70% + tuuleton → huippu
    source: src:PIE1
  rat_sign_threshold:
    value: Yksikin rotanjätös sisätiloissa → välitön toimenpide
    action: Ammattilainen paikalle, elintarvikkeet turvaan
    source: src:PIE1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Hiirten talvivaurioiden tarkistus puutarhassa. Punkkikausi
    alkaa. Muurahaiskartoitus rakennusten seinustoilla.'
  source: src:PIE1
- season: Kesä
  action: '[vko 22-35] Hyttystorjunta (seisova vesi pois), ampiaispesien kartoitus
    heinäkuusta, kärpästen torjunta (kompostialueet).'
  source: src:PIE1
- season: Syksy
  action: Hiirten sisääntunkeutumisen esto — tiivistä aukot <6mm. Rotat etsivät suojaa.
    Punkkikausi jatkuu lokakuuhun.
  source: src:PIE1
- season: Talvi
  action: Myyrien lumireiät ja jäljet seurannassa. Loukut tarkistetaan 2x/vko. Hiiret
    aktiivisia sisätiloissa.
  source: src:PIE1
FAILURE_MODES:
- mode: Rottahavainto
  detection: Jätökset (12-18mm, tummat) tai kaivautumisreiät perustuksissa
  action: Ammattimainen rotantorjunta VÄLITTÖMÄSTI, ilmoita terveystarkastajalle tarvittaessa
  source: src:PIE1
- mode: Ampiaispesä seinärakenteessa
  detection: Ampiaiset lentävät rakenteen sisään/ulos
  action: ÄLÄ tuki reikää → ampiaiset tulevat sisäkautta. Kutsu tuholaistorjuja.
  source: src:PIE1
PROCESS_FLOWS:
- flow_id: FLOW_PIEN_01
  trigger: mouse_activity_threshold ylittää kynnysarvon (Jätöksiä >10 kpl / m² tai
    purusahanjauhoa rakenteissa)
  action: Aseta loukkuja, tarkista rakenteiden tiiveys, ilmoita timpurille
  output: Tilanneraportti
  source: src:PIEN
- flow_id: FLOW_PIEN_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Hiirten talvivaurioiden tarkistus puutarhassa. Punkkikausi
    alkaa. Muurahaiskartoitus rak'
  output: Tarkistuslista
  source: src:PIEN
- flow_id: FLOW_PIEN_03
  trigger: 'Havaittu: Rottahavainto'
  action: Ammattimainen rotantorjunta VÄLITTÖMÄSTI, ilmoita terveystarkastajalle tarvittaessa
  output: Poikkeamaraportti
  source: src:PIEN
- flow_id: FLOW_PIEN_04
  trigger: Säännöllinen heartbeat
  action: 'pienelain_tuholais: rutiiniarviointi'
  output: Status-raportti
  source: src:PIEN
KNOWLEDGE_TABLES:
  common_pests:
  - pest: Metsämyyrä (Myodes glareolus)
    risk: Puunkuoren kalvaminen talvella, jäljet puutarhassa
    control: Suojukset taimiin, loukkupyynti
    source: src:PIE1
  - pest: Kotihiiri (Mus musculus)
    risk: Elintarvikkeet, johdot, eristeet
    control: Loukkupyynti, tiivistäminen
    source: src:PIE1
  - pest: Puutiainen (Ixodes ricinus)
    risk: Borrelioosi, TBE
    control: Repellentti, pukeutuminen, tarkistus
    source: src:PIE2
  - pest: Yleinen ampiainen (Vespula vulgaris)
    risk: Pistot, allergiariski
    control: Pesän poisto (ammattilainen), syöttiloukut
    source: src:PIE1
  - pest: Muurahainen (Camponotus spp.)
    risk: Puurakenteissa → rakennevauriot
    control: Paikanna pesä, boorihapon syötti
    source: src:PIE1
COMPLIANCE_AND_LEGAL:
  rodenticides: Jyrsijämyrkkyjen ammattimainen käyttö vaatii tuholaistorjuja-tutkinnon
    (Tukes) [src:PIE3]
  protected_species: Liito-orava, lepakot — suojeltuja, pesäpaikkoja ei saa tuhota
    [src:PIE4]
UNCERTAINTY_NOTES:
- Punkin levittämien tautien esiintyvyys vaihtelee alueittain — TBE-riski Kaakkois-Suomessa
  matala mutta kasvava.
- Myyräsyklin huippuvuosien ennustaminen on epätarkkaa.
SOURCE_REGISTRY:
  sources:
  - id: src:PIE1
    org: Tukes
    title: Tuholaistorjunta
    year: 2025
    url: https://tukes.fi/kemikaalit/biosidivalmisteet/tuholaistorjunta
    supports: Jyrsijätorjunta, ampiaistorjunta, muurahaiset.
  - id: src:PIE2
    org: THL
    title: Puutiaisten levittämät taudit
    year: 2025
    url: https://thl.fi/fi/web/infektiotaudit-ja-rokotukset/taudit-ja-torjunta/taudit-ja-taudinaiheuttajat-a-o/puutiaisaivotulehdus
    supports: Borrelioosi, TBE, punkkien aktiivisuuskaudet.
  - id: src:PIE3
    org: Tukes
    title: Tuholaistorjujatutkinto
    year: 2025
    url: https://tukes.fi/kemikaalit/biosidivalmisteet/tuholaistorjuja
    supports: Ammattimaisen torjunnan pätevyysvaatimukset.
  - id: src:PIE4
    org: Oikeusministeriö
    title: Luonnonsuojelulaki 9/2023
    year: 2023
    url: https://www.finlex.fi/fi/laki/ajantasa/2023/20230009
    supports: Suojellut eläinlajit, pesäpaikkojen turvaaminen.
eval_questions:
- q: Mikä on hiirihavainnon kynnys toimenpiteelle?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mouse_activity_threshold.value
  source: src:PIE1
- q: Milloin punkkikausi alkaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.tick_risk_level.value
  source: src:PIE2
- q: Mikä on ampiaispesän vähimmäisetäisyys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wasp_nest_proximity_m.value
  source: src:PIE1
- q: Milloin hyttyset ovat aktiivisimmillaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mosquito_peak_conditions.value
  source: src:PIE1
- q: Mitä tehdään rottahavainnossa?
  a_ref: FAILURE_MODES[0].action
  source: src:PIE1
- q: Mikä on rotan jätöksen koko?
  a_ref: FAILURE_MODES[0].detection
  source: src:PIE1
- q: Saako ampiaispesän reiän tukkia?
  a_ref: FAILURE_MODES[1].action
  source: src:PIE1
- q: Vaatiiko jyrsijämyrkyn käyttö tutkinnon?
  a_ref: COMPLIANCE_AND_LEGAL.rodenticides
  source: src:PIE3
- q: Onko liito-orava suojeltu?
  a_ref: COMPLIANCE_AND_LEGAL.protected_species
  source: src:PIE4
- q: Mikä aukon koko estää hiiren sisäänpääsyn?
  a_ref: SEASONAL_RULES[2].action
  source: src:PIE1
- q: Miten metsämyyrä tunnistetaan?
  a_ref: KNOWLEDGE_TABLES.common_pests[0].risk
  source: src:PIE1
- q: Mikä on puutiaisen terveysriski?
  a_ref: KNOWLEDGE_TABLES.common_pests[2].risk
  source: src:PIE2
- q: Miten muurahaispesä puurakenteessa havaitaan?
  a_ref: KNOWLEDGE_TABLES.common_pests[4].risk
  source: src:PIE1
- q: Kuinka usein loukkuja tarkistetaan talvella?
  a_ref: SEASONAL_RULES[3].action
  source: src:PIE1
- q: Mistä seisova vesi poistetaan kesällä?
  a_ref: SEASONAL_RULES[1].action
  source: src:PIE1
- q: Milloin ampiaispesien kartoitus tehdään?
  a_ref: SEASONAL_RULES[1].action
  source: src:PIE1
- q: Mikä torjuntakeino muurahaisille?
  a_ref: KNOWLEDGE_TABLES.common_pests[4].control
  source: src:PIE1
- q: Mitä hiiri tuhoaa sisätiloissa?
  a_ref: KNOWLEDGE_TABLES.common_pests[1].risk
  source: src:PIE1
- q: Mikä on TBE-riski Kaakkois-Suomessa?
  a_ref: UNCERTAINTY_NOTES
  source: src:PIE2
- q: Milloin metsämyyrän tuhoja tarkistetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:PIE1
- q: Mikä on ampiaisen riski?
  a_ref: KNOWLEDGE_TABLES.common_pests[3].risk
  source: src:PIE1
- q: Miten ratanjätös eroaa hiiren jätöksestä?
  a_ref: FAILURE_MODES[0].detection
  source: src:PIE1
- q: Kenelle hiirituhoista ilmoitetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.mouse_activity_threshold.action
  source: src:PIE1
- q: Onko myyräsykli ennustettavissa?
  a_ref: UNCERTAINTY_NOTES
  source: src:PIE1
- q: Milloin punkkikausi päättyy?
  a_ref: SEASONAL_RULES[2].action
  source: src:PIE1
- q: Miten hiiren sisäänpääsy estetään?
  a_ref: SEASONAL_RULES[2].action
  source: src:PIE1
- q: Mikä on hiiriloukkupyynnin ensisijainen keino?
  a_ref: KNOWLEDGE_TABLES.common_pests[1].control
  source: src:PIE1
- q: Onko lepakko suojeltu?
  a_ref: COMPLIANCE_AND_LEGAL.protected_species
  source: src:PIE4
- q: Mikä on ampiaisen torjuntakeino?
  a_ref: KNOWLEDGE_TABLES.common_pests[3].control
  source: src:PIE1
- q: Mitä keväällä tarkistetaan muurahaisista?
  a_ref: SEASONAL_RULES[0].action
  source: src:PIE1
- q: Milloin rotat etsivät suojaa?
  a_ref: SEASONAL_RULES[2].action
  source: src:PIE1
- q: Mikä on punkin torjuntakeino?
  a_ref: KNOWLEDGE_TABLES.common_pests[2].control
  source: src:PIE2
- q: Mitä puutiainen levittää?
  a_ref: KNOWLEDGE_TABLES.common_pests[2].risk
  source: src:PIE2
- q: Kenelle rotasta ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:PIE1
- q: Miten kompostialueen kärpäset torjutaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:PIE1
- q: Mikä on myyrien torjuntakeino talvella?
  a_ref: SEASONAL_RULES[3].action
  source: src:PIE1
- q: Voiko metsämyyrä vahingoittaa puutarhaa?
  a_ref: KNOWLEDGE_TABLES.common_pests[0].risk
  source: src:PIE1
- q: Kenelle ampiaispesästä seinärakenteessa ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:PIE1
- q: Mikä on ensisijainen torjuntaperiaate?
  a_ref: ASSUMPTIONS
  source: src:PIE1
- q: Mikä on rottahavainnon prioriteetti?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.rat_sign_threshold.action
  source: src:PIE1
```

**sources.yaml:**
```yaml
sources:
- id: src:PIE1
  org: Tukes
  title: Tuholaistorjunta
  year: 2025
  url: https://tukes.fi/kemikaalit/biosidivalmisteet/tuholaistorjunta
  supports: Jyrsijätorjunta, ampiaistorjunta, muurahaiset.
- id: src:PIE2
  org: THL
  title: Puutiaisten levittämät taudit
  year: 2025
  url: https://thl.fi/fi/web/infektiotaudit-ja-rokotukset/taudit-ja-torjunta/taudit-ja-taudinaiheuttajat-a-o/puutiaisaivotulehdus
  supports: Borrelioosi, TBE, punkkien aktiivisuuskaudet.
- id: src:PIE3
  org: Tukes
  title: Tuholaistorjujatutkinto
  year: 2025
  url: https://tukes.fi/kemikaalit/biosidivalmisteet/tuholaistorjuja
  supports: Ammattimaisen torjunnan pätevyysvaatimukset.
- id: src:PIE4
  org: Oikeusministeriö
  title: Luonnonsuojelulaki 9/2023
  year: 2023
  url: https://www.finlex.fi/fi/laki/ajantasa/2023/20230009
  supports: Suojellut eläinlajit, pesäpaikkojen turvaaminen.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Pieneläin- ja tuholaisasiantuntija
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenrannan mökkiympäristö — metsä ja järvi
- Jyrsijät, kärpäset, hyttyset, punkit, muurahaiset, ampiaisen
- Torjunta ensisijaisesti mekaanisin ja biologisin keinoin

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| mouse_activity_threshold | Jätöksiä >10 kpl / m² tai purusahanjauhoa rakenteissa | Aseta loukkuja, tarkista rakenteiden tiiveys, ilmoita timpurille | src:PIE1 |
| tick_risk_level | Aktiivinen kun vrk-keskilämpö >5°C (huhti–marras) | Punkkitarkistus ulkoilun jälkeen, repellenttiä | src:PIE2 |
| wasp_nest_proximity_m | 5 | Ampiaispesä <5m oleskelualueesta → poisto tai merkintä | src:PIE1 |
| mosquito_peak_conditions | Iltalämpö >15°C + kosteus >70% + tuuleton → huippu | — | src:PIE1 |
| rat_sign_threshold | Yksikin rotanjätös sisätiloissa → välitön toimenpide | Ammattilainen paikalle, elintarvikkeet turvaan | src:PIE1 |

## Tietotaulukot

**common_pests:**

| pest | risk | control | source |
| --- | --- | --- | --- |
| Metsämyyrä (Myodes glareolus) | Puunkuoren kalvaminen talvella, jäljet puutarhassa | Suojukset taimiin, loukkupyynti | src:PIE1 |
| Kotihiiri (Mus musculus) | Elintarvikkeet, johdot, eristeet | Loukkupyynti, tiivistäminen | src:PIE1 |
| Puutiainen (Ixodes ricinus) | Borrelioosi, TBE | Repellentti, pukeutuminen, tarkistus | src:PIE2 |
| Yleinen ampiainen (Vespula vulgaris) | Pistot, allergiariski | Pesän poisto (ammattilainen), syöttiloukut | src:PIE1 |
| Muurahainen (Camponotus spp.) | Puurakenteissa → rakennevauriot | Paikanna pesä, boorihapon syötti | src:PIE1 |

## Prosessit

**FLOW_PIEN_01:** mouse_activity_threshold ylittää kynnysarvon (Jätöksiä >10 kpl / m² tai purusahanjauhoa rakenteissa)
  → Aseta loukkuja, tarkista rakenteiden tiiveys, ilmoita timpurille
  Tulos: Tilanneraportti

**FLOW_PIEN_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Hiirten talvivaurioiden tarkistus puutarhassa. Punkkikausi alkaa. Muurahaiskartoitus rak
  Tulos: Tarkistuslista

**FLOW_PIEN_03:** Havaittu: Rottahavainto
  → Ammattimainen rotantorjunta VÄLITTÖMÄSTI, ilmoita terveystarkastajalle tarvittaessa
  Tulos: Poikkeamaraportti

**FLOW_PIEN_04:** Säännöllinen heartbeat
  → pienelain_tuholais: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Hiirten talvivaurioiden tarkistus puutarhassa. Punkkikausi alkaa. Muurahaiskartoitus rakennusten seinustoilla. | src:PIE1 |
| **Kesä** | [vko 22-35] Hyttystorjunta (seisova vesi pois), ampiaispesien kartoitus heinäkuusta, kärpästen torjunta (kompostialueet). | src:PIE1 |
| **Syksy** | Hiirten sisääntunkeutumisen esto — tiivistä aukot <6mm. Rotat etsivät suojaa. Punkkikausi jatkuu lokakuuhun. | src:PIE1 |
| **Talvi** | Myyrien lumireiät ja jäljet seurannassa. Loukut tarkistetaan 2x/vko. Hiiret aktiivisia sisätiloissa. | src:PIE1 |

## Virhe- ja vaaratilanteet

### ⚠️ Rottahavainto
- **Havaitseminen:** Jätökset (12-18mm, tummat) tai kaivautumisreiät perustuksissa
- **Toimenpide:** Ammattimainen rotantorjunta VÄLITTÖMÄSTI, ilmoita terveystarkastajalle tarvittaessa
- **Lähde:** src:PIE1

### ⚠️ Ampiaispesä seinärakenteessa
- **Havaitseminen:** Ampiaiset lentävät rakenteen sisään/ulos
- **Toimenpide:** ÄLÄ tuki reikää → ampiaiset tulevat sisäkautta. Kutsu tuholaistorjuja.
- **Lähde:** src:PIE1

## Lait ja vaatimukset
- **rodenticides:** Jyrsijämyrkkyjen ammattimainen käyttö vaatii tuholaistorjuja-tutkinnon (Tukes) [src:PIE3]
- **protected_species:** Liito-orava, lepakot — suojeltuja, pesäpaikkoja ei saa tuhota [src:PIE4]

## Epävarmuudet
- Punkin levittämien tautien esiintyvyys vaihtelee alueittain — TBE-riski Kaakkois-Suomessa matala mutta kasvava.
- Myyräsyklin huippuvuosien ennustaminen on epätarkkaa.

## Lähteet
- **src:PIE1**: Tukes — *Tuholaistorjunta* (2025) https://tukes.fi/kemikaalit/biosidivalmisteet/tuholaistorjunta
- **src:PIE2**: THL — *Puutiaisten levittämät taudit* (2025) https://thl.fi/fi/web/infektiotaudit-ja-rokotukset/taudit-ja-torjunta/taudit-ja-taudinaiheuttajat-a-o/puutiaisaivotulehdus
- **src:PIE3**: Tukes — *Tuholaistorjujatutkinto* (2025) https://tukes.fi/kemikaalit/biosidivalmisteet/tuholaistorjuja
- **src:PIE4**: Oikeusministeriö — *Luonnonsuojelulaki 9/2023* (2023) https://www.finlex.fi/fi/laki/ajantasa/2023/20230009

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on hiirihavainnon kynnys toimenpiteelle?**
    → `DECISION_METRICS_AND_THRESHOLDS.mouse_activity_threshold.value` [src:PIE1]
 2. **Milloin punkkikausi alkaa?**
    → `DECISION_METRICS_AND_THRESHOLDS.tick_risk_level.value` [src:PIE2]
 3. **Mikä on ampiaispesän vähimmäisetäisyys?**
    → `DECISION_METRICS_AND_THRESHOLDS.wasp_nest_proximity_m.value` [src:PIE1]
 4. **Milloin hyttyset ovat aktiivisimmillaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.mosquito_peak_conditions.value` [src:PIE1]
 5. **Mitä tehdään rottahavainnossa?**
    → `FAILURE_MODES[0].action` [src:PIE1]
 6. **Mikä on rotan jätöksen koko?**
    → `FAILURE_MODES[0].detection` [src:PIE1]
 7. **Saako ampiaispesän reiän tukkia?**
    → `FAILURE_MODES[1].action` [src:PIE1]
 8. **Vaatiiko jyrsijämyrkyn käyttö tutkinnon?**
    → `COMPLIANCE_AND_LEGAL.rodenticides` [src:PIE3]
 9. **Onko liito-orava suojeltu?**
    → `COMPLIANCE_AND_LEGAL.protected_species` [src:PIE4]
10. **Mikä aukon koko estää hiiren sisäänpääsyn?**
    → `SEASONAL_RULES[2].action` [src:PIE1]
11. **Miten metsämyyrä tunnistetaan?**
    → `KNOWLEDGE_TABLES.common_pests[0].risk` [src:PIE1]
12. **Mikä on puutiaisen terveysriski?**
    → `KNOWLEDGE_TABLES.common_pests[2].risk` [src:PIE2]
13. **Miten muurahaispesä puurakenteessa havaitaan?**
    → `KNOWLEDGE_TABLES.common_pests[4].risk` [src:PIE1]
14. **Kuinka usein loukkuja tarkistetaan talvella?**
    → `SEASONAL_RULES[3].action` [src:PIE1]
15. **Mistä seisova vesi poistetaan kesällä?**
    → `SEASONAL_RULES[1].action` [src:PIE1]
16. **Milloin ampiaispesien kartoitus tehdään?**
    → `SEASONAL_RULES[1].action` [src:PIE1]
17. **Mikä torjuntakeino muurahaisille?**
    → `KNOWLEDGE_TABLES.common_pests[4].control` [src:PIE1]
18. **Mitä hiiri tuhoaa sisätiloissa?**
    → `KNOWLEDGE_TABLES.common_pests[1].risk` [src:PIE1]
19. **Mikä on TBE-riski Kaakkois-Suomessa?**
    → `UNCERTAINTY_NOTES` [src:PIE2]
20. **Milloin metsämyyrän tuhoja tarkistetaan?**
    → `SEASONAL_RULES[0].action` [src:PIE1]
21. **Mikä on ampiaisen riski?**
    → `KNOWLEDGE_TABLES.common_pests[3].risk` [src:PIE1]
22. **Miten ratanjätös eroaa hiiren jätöksestä?**
    → `FAILURE_MODES[0].detection` [src:PIE1]
23. **Kenelle hiirituhoista ilmoitetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.mouse_activity_threshold.action` [src:PIE1]
24. **Onko myyräsykli ennustettavissa?**
    → `UNCERTAINTY_NOTES` [src:PIE1]
25. **Milloin punkkikausi päättyy?**
    → `SEASONAL_RULES[2].action` [src:PIE1]
26. **Miten hiiren sisäänpääsy estetään?**
    → `SEASONAL_RULES[2].action` [src:PIE1]
27. **Mikä on hiiriloukkupyynnin ensisijainen keino?**
    → `KNOWLEDGE_TABLES.common_pests[1].control` [src:PIE1]
28. **Onko lepakko suojeltu?**
    → `COMPLIANCE_AND_LEGAL.protected_species` [src:PIE4]
29. **Mikä on ampiaisen torjuntakeino?**
    → `KNOWLEDGE_TABLES.common_pests[3].control` [src:PIE1]
30. **Mitä keväällä tarkistetaan muurahaisista?**
    → `SEASONAL_RULES[0].action` [src:PIE1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 9
## AGENT 9: Entomologi (Hyönteistutkija)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: entomologi
  agent_name: Entomologi (Hyönteistutkija)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Fokus: mehiläisiin vaikuttavat hyönteiset + kasvintuholaiset + metsätuholaiset'
- Korvenrannan lähiympäristö, vyöhyke II-III
- Kytkentä tarhaaja-, metsänhoitaja- ja hortonomi-agentteihin
DECISION_METRICS_AND_THRESHOLDS:
  varroa_mite_threshold_per_100_bees:
    value: 3
    action: '>3/100 → kemiallinen hoito (amitraz/oksaalihappo). <1/100 → seuranta
      riittää. Hoitoajankohta: elokuu (satokehysten poiston jälkeen).'
    source: src:ENT1
  bark_beetle_trap_threshold:
    value: Feromonipyydyksessä >500 kirjanpainajaa / 2 viikkoa → hakkuuhälytys
    source: src:ENT2
    action: '>500/2vko → hakkuuhälytys metsänhoitajalle. Poista tuoreita kaatopuita
      riskialueelta HETI.'
  pollinator_diversity_index:
    value: Shannon H' >2.0 normaali, <1.5 hälytys
    action: H'<1.5 → ekologinen hälytys, selvitä syy (torjunta-aine, elinympäristömuutos).
      H'>2.0 → normaali.
    source: src:ENT1
  aphid_colony_density:
    value: '>50 kirvaa / verso → biologinen torjunta (leppäpirkot)'
    source: src:ENT3
  wax_moth_detection:
    value: Toukkien seittiverkkoa kehyksillä → puhdista ja pakasta kehykset -18°C
      48h
    source: src:ENT1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Varroa-pudotusalustan asennus huhti-toukokuussa. Kirjanpainaja-pyydykset
    paikoilleen toukokuussa.'
  source: src:ENT1
- season: Kesä
  action: '[vko 22-35] Kirvapopulaation seuranta, leppäpirkkojen suojelu (EI laaja-alaista
    torjuntaruiskutusta). Varroa-luontainen pudotus seurannassa.'
  source: src:ENT1
- season: Syksy
  action: '[vko 36-48] Varroa-hoito oksaalihapolla (pesimättömänä aikana). Vahakoi-kehysten
    pakastus ennen varastointia.'
  source: src:ENT1
- season: Talvi
  action: '[vko 49-13] Kehysvarastojen seuranta (vahakoi, hiiret). Varroahoitotuloksen
    arviointi keväällä.'
  source: src:ENT1
FAILURE_MODES:
- mode: Varroa-hoito epäonnistunut
  detection: Pudotusmäärä edelleen >3/100 hoidon jälkeen
  action: Toinen hoitokierros eri valmisteella. Ilmoita tautivahti-agentille.
  source: src:ENT1
- mode: Kirjanpainajaepidemia
  detection: '>500 yksilöä pyydyksessä tai useita kuivuvia kuusia'
  action: Välitön puunkaato. Ilmoita metsänhoitajalle. Kaarnan poltto.
  source: src:ENT2
PROCESS_FLOWS:
- flow_id: FLOW_ENTO_01
  trigger: varroa_mite_threshold_per_100_bees ylittää kynnysarvon (3)
  action: '>3/100 → kemiallinen hoito (amitraz/oksaalihappo). <1/100 → seuranta riittää.
    Hoitoajankohta: elokuu (satokehysten poiston jälkeen).'
  output: Tilanneraportti
  source: src:ENTO
- flow_id: FLOW_ENTO_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Varroa-pudotusalustan asennus huhti-toukokuussa. Kirjanpainaja-pyydykset
    paikoilleen tou'
  output: Tarkistuslista
  source: src:ENTO
- flow_id: FLOW_ENTO_03
  trigger: 'Havaittu: Varroa-hoito epäonnistunut'
  action: Toinen hoitokierros eri valmisteella. Ilmoita tautivahti-agentille.
  output: Poikkeamaraportti
  source: src:ENTO
- flow_id: FLOW_ENTO_04
  trigger: Säännöllinen heartbeat
  action: 'entomologi: rutiiniarviointi'
  output: Status-raportti
  source: src:ENTO
KNOWLEDGE_TABLES:
  key_insects:
  - insect: Varroapunkki (Varroa destructor)
    role: Mehiläisparasiitti
    severity: KRIITTINEN
    monitoring: Pudotusalustamittaus, sokerijauhomenetelmä
    source: src:ENT1
  - insect: Kirjanpainaja (Ips typographus)
    role: Kuusen tuholainen
    severity: KORKEA
    monitoring: Feromonipyydykset touko-elokuussa
    source: src:ENT2
  - insect: Leppäpirkko (Coccinellidae)
    role: Kirvansyöjä (biologinen torjunta)
    severity: HYÖDYLLINEN
    monitoring: Populaatioseuranta puutarhassa
    source: src:ENT3
  - insect: Vahakoi (Galleria mellonella)
    role: Mehiläispesän tuholainen
    severity: KESKITASO
    monitoring: Varastoitujen kehysten tarkistus
    source: src:ENT1
  - insect: Pieni pesäkuoriainen (Aethina tumida)
    role: Mehiläisparasiitti (EI vielä Suomessa)
    severity: POTENTIAALINEN
    monitoring: EU-tarkkailu, ilmoita havainnoista Ruokavirastolle
    source: src:ENT4
COMPLIANCE_AND_LEGAL:
  varroa_treatment: Lääkinnälliset varroa-valmisteet vain eläinlääkkeinä hyväksytyt
    (Ruokavirasto) [src:ENT4]
  pesticide_reporting: 'Kasvinsuojeluainerekisteri (Tukes): vain hyväksytyt valmisteet
    [src:ENT3]'
UNCERTAINTY_NOTES:
- Pieni pesäkuoriainen voi levitä Suomeen ilmastonmuutoksen myötä — seuranta tärkeä.
- Varroa-resistenssi hoitoaineille kasvava ongelma Euroopassa.
SOURCE_REGISTRY:
  sources:
  - id: src:ENT1
    org: Ruokavirasto
    title: Mehiläisten terveys ja taudit
    year: 2025
    url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
    supports: Varroa, vahakoi, pesäkuoriainen.
  - id: src:ENT2
    org: Luonnonvarakeskus (Luke)
    title: Kirjanpainajan torjunta
    year: 2024
    url: https://www.luke.fi/fi/tutkimus/metsakasvinsuojelu
    supports: Kirjanpainaja, feromonipyydykset, epidemiarajat.
  - id: src:ENT3
    org: Tukes
    title: Kasvinsuojeluainerekisteri
    year: 2025
    url: https://tukes.fi/kemikaalit/kasvinsuojeluaineet
    supports: Hyväksytyt valmisteet, biologinen torjunta.
  - id: src:ENT4
    org: Ruokavirasto
    title: Aethina tumida -seuranta
    year: 2025
    url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
    supports: Pieni pesäkuoriainen, EU-seuranta.
eval_questions:
- q: Mikä on varroa-kynnys hoitotoimenpiteelle?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.varroa_mite_threshold_per_100_bees.value
  source: src:ENT1
- q: Mikä on kirjanpainajapyydyksen hälytysraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bark_beetle_trap_threshold.value
  source: src:ENT2
- q: Mikä on hyvä pölyttäjädiversiteetti (Shannon H')?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pollinator_diversity_index.value
  source: src:ENT1
- q: Mikä on kirvatiheyden hälytysraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.aphid_colony_density.value
  source: src:ENT3
- q: Miten vahakoi havaitaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wax_moth_detection.value
  source: src:ENT1
- q: Miten varroa-pudotusta seurataan?
  a_ref: KNOWLEDGE_TABLES.key_insects[0].monitoring
  source: src:ENT1
- q: Onko pieni pesäkuoriainen Suomessa?
  a_ref: KNOWLEDGE_TABLES.key_insects[4].role
  source: src:ENT4
- q: Milloin varroa-hoito oksaalihapolla tehdään?
  a_ref: SEASONAL_RULES[2].action
  source: src:ENT1
- q: Mitä tehdään epäonnistuneen varroa-hoidon jälkeen?
  a_ref: FAILURE_MODES[0].action
  source: src:ENT1
- q: Milloin kirjanpainajapyydykset asennetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:ENT1
- q: Miksi laaja-alaista ruiskutusta vältetään?
  a_ref: SEASONAL_RULES[1].action
  source: src:ENT1
- q: Miten vahakoidelta suojaudutaan talvella?
  a_ref: SEASONAL_RULES[3].action
  source: src:ENT1
- q: Mikä on leppäpirkon hyöty?
  a_ref: KNOWLEDGE_TABLES.key_insects[2].role
  source: src:ENT3
- q: Kenelle kirjanpainajaepidemiasta ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:ENT2
- q: Ovatko varroa-valmisteet vapaasti ostettavissa?
  a_ref: COMPLIANCE_AND_LEGAL.varroa_treatment
  source: src:ENT4
- q: Mikä on varroapunkin vakavuusaste?
  a_ref: KNOWLEDGE_TABLES.key_insects[0].severity
  source: src:ENT1
- q: Miten kirjanpainajaa seurataan?
  a_ref: KNOWLEDGE_TABLES.key_insects[1].monitoring
  source: src:ENT2
- q: Millä lämpötilalla vahakoidevelykset tuhotaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wax_moth_detection.value
  source: src:ENT1
- q: Onko varroa-resistenssi ongelma?
  a_ref: UNCERTAINTY_NOTES
  source: src:ENT1
- q: Kenelle varroa-epäonnistumisesta ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:ENT1
- q: Mikä on kirvankontrollin biologinen keino?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.aphid_colony_density.value
  source: src:ENT3
- q: Mikä taho hyväksyy kasvinsuojeluaineet?
  a_ref: COMPLIANCE_AND_LEGAL.pesticide_reporting
  source: src:ENT3
- q: Mitä tehdään kirjanpainajaepidemiassa?
  a_ref: FAILURE_MODES[1].action
  source: src:ENT2
- q: Onko vahakoi kriittinen tuholainen?
  a_ref: KNOWLEDGE_TABLES.key_insects[3].severity
  source: src:ENT1
- q: Kenelle pienestä pesäkuoriaisesta ilmoitetaan?
  a_ref: KNOWLEDGE_TABLES.key_insects[4].monitoring
  source: src:ENT4
- q: Mikä on kirjanpainajan merkitys metsätaloudelle?
  a_ref: KNOWLEDGE_TABLES.key_insects[1].severity
  source: src:ENT2
- q: Miten alhainen pölyttäjädiversiteetti korjataan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pollinator_diversity_index.action
  source: src:ENT1
- q: Milloin varroa-pudotusalusta asennetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:ENT1
- q: Mikä on sokerijauhomenetelmä?
  a_ref: KNOWLEDGE_TABLES.key_insects[0].monitoring
  source: src:ENT1
- q: Voiko pieni pesäkuoriainen levitä Suomeen?
  a_ref: UNCERTAINTY_NOTES
  source: src:ENT4
- q: Mikä on varroa-hoidon onnistumisen mittari?
  a_ref: FAILURE_MODES[0].detection
  source: src:ENT1
- q: Mihin toimenpiteeseen >500 kirjanpainajaa johtaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bark_beetle_trap_threshold.value
  source: src:ENT2
- q: Miten kaarnaa käsitellään kirjanpainajatuhon jälkeen?
  a_ref: FAILURE_MODES[1].action
  source: src:ENT2
- q: Milloin kirjanpainajia seurataan?
  a_ref: KNOWLEDGE_TABLES.key_insects[1].monitoring
  source: src:ENT2
- q: Mikä on vahakoin tunnistustapa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wax_moth_detection.value
  source: src:ENT1
- q: Mitä kehyksille tehdään ennen varastointia?
  a_ref: SEASONAL_RULES[2].action
  source: src:ENT1
- q: Mikä on varroa-pudotuksen merkitys kesällä?
  a_ref: SEASONAL_RULES[1].action
  source: src:ENT1
- q: Miten leppäpirkkoja suojellaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:ENT1
- q: Onko pieni pesäkuoriainen EU:n tarkkailussa?
  a_ref: KNOWLEDGE_TABLES.key_insects[4].monitoring
  source: src:ENT4
- q: Mikä on Shannon diversiteetti-indeksin hälytystaso?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pollinator_diversity_index.value
  source: src:ENT1
```

**sources.yaml:**
```yaml
sources:
- id: src:ENT1
  org: Ruokavirasto
  title: Mehiläisten terveys ja taudit
  year: 2025
  url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
  supports: Varroa, vahakoi, pesäkuoriainen.
- id: src:ENT2
  org: Luonnonvarakeskus (Luke)
  title: Kirjanpainajan torjunta
  year: 2024
  url: https://www.luke.fi/fi/tutkimus/metsakasvinsuojelu
  supports: Kirjanpainaja, feromonipyydykset, epidemiarajat.
- id: src:ENT3
  org: Tukes
  title: Kasvinsuojeluainerekisteri
  year: 2025
  url: https://tukes.fi/kemikaalit/kasvinsuojeluaineet
  supports: Hyväksytyt valmisteet, biologinen torjunta.
- id: src:ENT4
  org: Ruokavirasto
  title: Aethina tumida -seuranta
  year: 2025
  url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
  supports: Pieni pesäkuoriainen, EU-seuranta.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Entomologi (Hyönteistutkija)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Fokus: mehiläisiin vaikuttavat hyönteiset + kasvintuholaiset + metsätuholaiset
- Korvenrannan lähiympäristö, vyöhyke II-III
- Kytkentä tarhaaja-, metsänhoitaja- ja hortonomi-agentteihin

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| varroa_mite_threshold_per_100_bees | 3 | >3/100 → kemiallinen hoito (amitraz/oksaalihappo). <1/100 → seuranta riittää. Hoitoajankohta: elokuu (satokehysten poiston jälkeen). | src:ENT1 |
| bark_beetle_trap_threshold | Feromonipyydyksessä >500 kirjanpainajaa / 2 viikkoa → hakkuuhälytys | >500/2vko → hakkuuhälytys metsänhoitajalle. Poista tuoreita kaatopuita riskialueelta HETI. | src:ENT2 |
| pollinator_diversity_index | Shannon H' >2.0 normaali, <1.5 hälytys | H'<1.5 → ekologinen hälytys, selvitä syy (torjunta-aine, elinympäristömuutos). H'>2.0 → normaali. | src:ENT1 |
| aphid_colony_density | >50 kirvaa / verso → biologinen torjunta (leppäpirkot) | — | src:ENT3 |
| wax_moth_detection | Toukkien seittiverkkoa kehyksillä → puhdista ja pakasta kehykset -18°C 48h | — | src:ENT1 |

## Tietotaulukot

**key_insects:**

| insect | role | severity | monitoring | source |
| --- | --- | --- | --- | --- |
| Varroapunkki (Varroa destructor) | Mehiläisparasiitti | KRIITTINEN | Pudotusalustamittaus, sokerijauhomenetelmä | src:ENT1 |
| Kirjanpainaja (Ips typographus) | Kuusen tuholainen | KORKEA | Feromonipyydykset touko-elokuussa | src:ENT2 |
| Leppäpirkko (Coccinellidae) | Kirvansyöjä (biologinen torjunta) | HYÖDYLLINEN | Populaatioseuranta puutarhassa | src:ENT3 |
| Vahakoi (Galleria mellonella) | Mehiläispesän tuholainen | KESKITASO | Varastoitujen kehysten tarkistus | src:ENT1 |
| Pieni pesäkuoriainen (Aethina tumida) | Mehiläisparasiitti (EI vielä Suomessa) | POTENTIAALINEN | EU-tarkkailu, ilmoita havainnoista Ruokavirastolle | src:ENT4 |

## Prosessit

**FLOW_ENTO_01:** varroa_mite_threshold_per_100_bees ylittää kynnysarvon (3)
  → >3/100 → kemiallinen hoito (amitraz/oksaalihappo). <1/100 → seuranta riittää. Hoitoajankohta: elokuu (satokehysten poiston jälkeen).
  Tulos: Tilanneraportti

**FLOW_ENTO_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Varroa-pudotusalustan asennus huhti-toukokuussa. Kirjanpainaja-pyydykset paikoilleen tou
  Tulos: Tarkistuslista

**FLOW_ENTO_03:** Havaittu: Varroa-hoito epäonnistunut
  → Toinen hoitokierros eri valmisteella. Ilmoita tautivahti-agentille.
  Tulos: Poikkeamaraportti

**FLOW_ENTO_04:** Säännöllinen heartbeat
  → entomologi: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Varroa-pudotusalustan asennus huhti-toukokuussa. Kirjanpainaja-pyydykset paikoilleen toukokuussa. | src:ENT1 |
| **Kesä** | [vko 22-35] Kirvapopulaation seuranta, leppäpirkkojen suojelu (EI laaja-alaista torjuntaruiskutusta). Varroa-luontainen pudotus seurannassa. | src:ENT1 |
| **Syksy** | [vko 36-48] Varroa-hoito oksaalihapolla (pesimättömänä aikana). Vahakoi-kehysten pakastus ennen varastointia. | src:ENT1 |
| **Talvi** | [vko 49-13] Kehysvarastojen seuranta (vahakoi, hiiret). Varroahoitotuloksen arviointi keväällä. | src:ENT1 |

## Virhe- ja vaaratilanteet

### ⚠️ Varroa-hoito epäonnistunut
- **Havaitseminen:** Pudotusmäärä edelleen >3/100 hoidon jälkeen
- **Toimenpide:** Toinen hoitokierros eri valmisteella. Ilmoita tautivahti-agentille.
- **Lähde:** src:ENT1

### ⚠️ Kirjanpainajaepidemia
- **Havaitseminen:** >500 yksilöä pyydyksessä tai useita kuivuvia kuusia
- **Toimenpide:** Välitön puunkaato. Ilmoita metsänhoitajalle. Kaarnan poltto.
- **Lähde:** src:ENT2

## Lait ja vaatimukset
- **varroa_treatment:** Lääkinnälliset varroa-valmisteet vain eläinlääkkeinä hyväksytyt (Ruokavirasto) [src:ENT4]
- **pesticide_reporting:** Kasvinsuojeluainerekisteri (Tukes): vain hyväksytyt valmisteet [src:ENT3]

## Epävarmuudet
- Pieni pesäkuoriainen voi levitä Suomeen ilmastonmuutoksen myötä — seuranta tärkeä.
- Varroa-resistenssi hoitoaineille kasvava ongelma Euroopassa.

## Lähteet
- **src:ENT1**: Ruokavirasto — *Mehiläisten terveys ja taudit* (2025) https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
- **src:ENT2**: Luonnonvarakeskus (Luke) — *Kirjanpainajan torjunta* (2024) https://www.luke.fi/fi/tutkimus/metsakasvinsuojelu
- **src:ENT3**: Tukes — *Kasvinsuojeluainerekisteri* (2025) https://tukes.fi/kemikaalit/kasvinsuojeluaineet
- **src:ENT4**: Ruokavirasto — *Aethina tumida -seuranta* (2025) https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on varroa-kynnys hoitotoimenpiteelle?**
    → `DECISION_METRICS_AND_THRESHOLDS.varroa_mite_threshold_per_100_bees.value` [src:ENT1]
 2. **Mikä on kirjanpainajapyydyksen hälytysraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.bark_beetle_trap_threshold.value` [src:ENT2]
 3. **Mikä on hyvä pölyttäjädiversiteetti (Shannon H')?**
    → `DECISION_METRICS_AND_THRESHOLDS.pollinator_diversity_index.value` [src:ENT1]
 4. **Mikä on kirvatiheyden hälytysraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.aphid_colony_density.value` [src:ENT3]
 5. **Miten vahakoi havaitaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.wax_moth_detection.value` [src:ENT1]
 6. **Miten varroa-pudotusta seurataan?**
    → `KNOWLEDGE_TABLES.key_insects[0].monitoring` [src:ENT1]
 7. **Onko pieni pesäkuoriainen Suomessa?**
    → `KNOWLEDGE_TABLES.key_insects[4].role` [src:ENT4]
 8. **Milloin varroa-hoito oksaalihapolla tehdään?**
    → `SEASONAL_RULES[2].action` [src:ENT1]
 9. **Mitä tehdään epäonnistuneen varroa-hoidon jälkeen?**
    → `FAILURE_MODES[0].action` [src:ENT1]
10. **Milloin kirjanpainajapyydykset asennetaan?**
    → `SEASONAL_RULES[0].action` [src:ENT1]
11. **Miksi laaja-alaista ruiskutusta vältetään?**
    → `SEASONAL_RULES[1].action` [src:ENT1]
12. **Miten vahakoidelta suojaudutaan talvella?**
    → `SEASONAL_RULES[3].action` [src:ENT1]
13. **Mikä on leppäpirkon hyöty?**
    → `KNOWLEDGE_TABLES.key_insects[2].role` [src:ENT3]
14. **Kenelle kirjanpainajaepidemiasta ilmoitetaan?**
    → `FAILURE_MODES[1].action` [src:ENT2]
15. **Ovatko varroa-valmisteet vapaasti ostettavissa?**
    → `COMPLIANCE_AND_LEGAL.varroa_treatment` [src:ENT4]
16. **Mikä on varroapunkin vakavuusaste?**
    → `KNOWLEDGE_TABLES.key_insects[0].severity` [src:ENT1]
17. **Miten kirjanpainajaa seurataan?**
    → `KNOWLEDGE_TABLES.key_insects[1].monitoring` [src:ENT2]
18. **Millä lämpötilalla vahakoidevelykset tuhotaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.wax_moth_detection.value` [src:ENT1]
19. **Onko varroa-resistenssi ongelma?**
    → `UNCERTAINTY_NOTES` [src:ENT1]
20. **Kenelle varroa-epäonnistumisesta ilmoitetaan?**
    → `FAILURE_MODES[0].action` [src:ENT1]
21. **Mikä on kirvankontrollin biologinen keino?**
    → `DECISION_METRICS_AND_THRESHOLDS.aphid_colony_density.value` [src:ENT3]
22. **Mikä taho hyväksyy kasvinsuojeluaineet?**
    → `COMPLIANCE_AND_LEGAL.pesticide_reporting` [src:ENT3]
23. **Mitä tehdään kirjanpainajaepidemiassa?**
    → `FAILURE_MODES[1].action` [src:ENT2]
24. **Onko vahakoi kriittinen tuholainen?**
    → `KNOWLEDGE_TABLES.key_insects[3].severity` [src:ENT1]
25. **Kenelle pienestä pesäkuoriaisesta ilmoitetaan?**
    → `KNOWLEDGE_TABLES.key_insects[4].monitoring` [src:ENT4]
26. **Mikä on kirjanpainajan merkitys metsätaloudelle?**
    → `KNOWLEDGE_TABLES.key_insects[1].severity` [src:ENT2]
27. **Miten alhainen pölyttäjädiversiteetti korjataan?**
    → `DECISION_METRICS_AND_THRESHOLDS.pollinator_diversity_index.action` [src:ENT1]
28. **Milloin varroa-pudotusalusta asennetaan?**
    → `SEASONAL_RULES[0].action` [src:ENT1]
29. **Mikä on sokerijauhomenetelmä?**
    → `KNOWLEDGE_TABLES.key_insects[0].monitoring` [src:ENT1]
30. **Voiko pieni pesäkuoriainen levitä Suomeen?**
    → `UNCERTAINTY_NOTES` [src:ENT4]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 10
## AGENT 10: Tähtitieteilijä
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: tahtitieteilija
  agent_name: Tähtitieteilijä
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 'Havainnointipaikka: Korvenranta, Kouvola (~60.9°N, 26.7°E)'
- Matala valosaaste (Bortle 3-4)
- PTZ-kamera preset 5 (taivasnäkymä) tai erillinen tähtitieteellinen kamera
DECISION_METRICS_AND_THRESHOLDS:
  aurora_kp_threshold:
    value: 3
    action: Kp≥3 → revontulimahdollisuus, ilmoita luontokuvaajalle. Kp≥5 → todennäköiset,
      PTZ pohjoiseen. Kp≥7 → poikkeuksellinen, kaikki ulos.
    source: src:TAH1
  seeing_arcsec:
    value: <3 arcsekuntia → hyvä, <2 → erinomainen
    source: src:TAH1
    action: <2" → erinomainen (planeetat). <3" → hyvä (syväavaruus). >4" → heikko,
      ei kannata teleskoopilla. Tarkista Meteoblue.
  light_pollution_bortle:
    value: 3-4 (maaseututason)
    note: Kouvolan keskustan valonlähde etelässä
    source: src:TAH2
  moon_illumination_limit:
    value: Kuun valaistus >50% → syväavaruuskohteet heikosti
    action: Ohjaa kuukuvausaiheisiin tai planetaarisiin kohteisiin
    source: src:TAH2
  meteor_shower_zenithal_rate:
    value: '>20 meteoria/h → mainitsemisen arvoinen, >100/h → hälytys (Persidit, Geminidit)'
    source: src:TAH2
    action: '>20/h → maininta käyttäjälle. >100/h (Perseidit 11-13.8) → HÄLYTYS luontokuvaajalle,
      valmista PTZ.'
  iss_pass_brightness_mag:
    value: <-2.0 → näkyvä silminnähden, ilmoita käyttäjälle
    source: src:TAH3
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Galaxikausi (Virgo/Coma-klusterit). Revontuliseuranta (equinox-efekti
    maalis-huhtikuussa). Lyridit huhtikuussa.'
  source: src:TAH2
- season: Kesä
  action: '[vko 22-35] Yötön yö: pimeimmilläänkin tähtivalokuvaus hankalaa kesä-heinäkuussa.
    NLC-pilvet (valaistut yöpilvet) seurannassa.'
  source: src:TAH2
- season: Syksy
  action: '[vko 36-48] Hyvä pimeys palaa syyskuussa. Perseidit elokuussa. Andromeda-galaksi
    korkealla.'
  source: src:TAH2
- season: Talvi
  action: '[vko 49-13] Paras havaintokausi: pitkät yöt, Orion, Geminidit. Pakkanen
    → kameran akku ja optiikan huurtuminen huomioitava.'
  source: src:TAH2
FAILURE_MODES:
- mode: Optiikka huurussa
  detection: Tähtikuvat sumeita, kastepiste lähellä
  action: Lämmityspanta linssille, tai kuivauspussi kameran viereen
  source: src:TAH2
- mode: Valosaastepiikki
  detection: Tausta-kirkkaus nousee odottamattomasti
  action: Tarkista suunta (vältä Kouvolan suuntaa), ilmoita valaistusmestari-agentille
  source: src:TAH2
PROCESS_FLOWS:
- flow_id: FLOW_TAHT_01
  trigger: aurora_kp_threshold ylittää kynnysarvon (3)
  action: Kp≥3 → revontulimahdollisuus, ilmoita luontokuvaajalle. Kp≥5 → todennäköiset,
    PTZ pohjoiseen. Kp≥7 → poikkeuksellinen, kaikki ulos.
  output: Tilanneraportti
  source: src:TAHT
- flow_id: FLOW_TAHT_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Galaxikausi (Virgo/Coma-klusterit). Revontuliseuranta (equinox-efekti
    maalis-huhtikuussa'
  output: Tarkistuslista
  source: src:TAHT
- flow_id: FLOW_TAHT_03
  trigger: 'Havaittu: Optiikka huurussa'
  action: Lämmityspanta linssille, tai kuivauspussi kameran viereen
  output: Poikkeamaraportti
  source: src:TAHT
- flow_id: FLOW_TAHT_04
  trigger: Säännöllinen heartbeat
  action: 'tahtitieteilija: rutiiniarviointi'
  output: Status-raportti
  source: src:TAHT
KNOWLEDGE_TABLES:
  annual_events:
  - event: Perseidit
    date: 11.-13.8.
    rate: 100-150/h
    source: src:TAH3
  - event: Geminidit
    date: 13.-14.12.
    rate: 120-150/h
    source: src:TAH3
  - event: Quadrantidit
    date: 3.-4.1.
    rate: 80-120/h
    source: src:TAH3
  - event: Lyridit
    date: 22.-23.4.
    rate: 15-20/h
    source: src:TAH3
  - event: Yötön yö (astron. hämärä ei pääty)
    date: ~25.5.–18.7. (60°N)
    note: Syväavaruuskuvaus mahdotonta
    source: src:TAH2
  - event: Paras pimeä kausi
    date: Joulu-tammikuu
    note: 17+ h pimeää, paras syväavaruuskausi
    source: src:TAH2
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Revontuliennusteet ovat luotettavia vain 1-2h ennakolta.
- Meteorimäärien ZHR on ideaaliolosuhdeluku — todellinen havaittava määrä 30-50% tästä.
SOURCE_REGISTRY:
  sources:
  - id: src:TAH1
    org: Ilmatieteen laitos
    title: Avaruussää ja revontulet
    year: 2025
    url: https://www.ilmatieteenlaitos.fi/revontulet
    supports: Kp-indeksi, revontuliennusteet, aurinkotuuli.
  - id: src:TAH2
    org: Tähtitieteellinen yhdistys Ursa
    title: Tähtitaivaan seuranta
    year: 2025
    url: https://www.ursa.fi/
    supports: Seeing, Bortle, havaintokohteet, fenologia.
  - id: src:TAH3
    org: International Meteor Organization
    title: Meteor Shower Calendar
    year: 2026
    url: https://www.imo.net/
    supports: Meteorisuihkut, ZHR-arvot, ajankohdat.
eval_questions:
- q: Mikä Kp-indeksi tarvitaan revontulille Huhdasjärvellä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.aurora_kp_threshold.value
  source: src:TAH1
- q: Milloin Perseidit ovat huipussaan?
  a_ref: KNOWLEDGE_TABLES.annual_events[0].date
  source: src:TAH3
- q: Mikä on Bortle-luokka Korvenrannassa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.light_pollution_bortle.value
  source: src:TAH2
- q: Milloin syväavaruuskuvaus on parhaimmillaan?
  a_ref: KNOWLEDGE_TABLES.annual_events[5]
  source: src:TAH2
- q: Milloin yötön yö alkaa 60°N?
  a_ref: KNOWLEDGE_TABLES.annual_events[4].date
  source: src:TAH2
- q: Mikä on ISS:n näkyvyysraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.iss_pass_brightness_mag.value
  source: src:TAH3
- q: Mikä kuun valaistus rajoittaa syväavaruuskuvausta?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.moon_illumination_limit.value
  source: src:TAH2
- q: Mikä on hyvä seeing-arvo?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.seeing_arcsec.value
  source: src:TAH2
- q: Milloin revontuliennusteet ovat luotettavimpia?
  a_ref: UNCERTAINTY_NOTES
  source: src:TAH1
- q: Mitä tehdään kun optiikka huurustuu?
  a_ref: FAILURE_MODES[0].action
  source: src:TAH2
- q: Mikä on Geminidien huippu?
  a_ref: KNOWLEDGE_TABLES.annual_events[1].date
  source: src:TAH3
- q: Mikä on Perseidien ZHR?
  a_ref: KNOWLEDGE_TABLES.annual_events[0].rate
  source: src:TAH3
- q: Miksi Kouvolan suuntaa vältetään?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.light_pollution_bortle.note
  source: src:TAH2
- q: Mitä NLC-pilvet ovat?
  a_ref: SEASONAL_RULES[1].action
  source: src:TAH2
- q: Mikä on paras galaksikuvauskausi?
  a_ref: SEASONAL_RULES[0].action
  source: src:TAH2
- q: Milloin equinox-efekti lisää revontulitodennäköisyyttä?
  a_ref: SEASONAL_RULES[0].action
  source: src:TAH2
- q: Mikä on meteorimäärien todellinen havaittavuus vs ZHR?
  a_ref: UNCERTAINTY_NOTES
  source: src:TAH3
- q: Mikä on Kp ≥5 merkitys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.aurora_kp_threshold.action
  source: src:TAH1
- q: Milloin pimeä kausi on pisin?
  a_ref: KNOWLEDGE_TABLES.annual_events[5].date
  source: src:TAH2
- q: Mitä talvella huomioidaan kameran kanssa?
  a_ref: SEASONAL_RULES[3].action
  source: src:TAH2
- q: Mikä on meteorisuihkun ilmoitusraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.meteor_shower_zenithal_rate.value
  source: src:TAH3
- q: Milloin Orion on parhaiten näkyvissä?
  a_ref: SEASONAL_RULES[3].action
  source: src:TAH2
- q: Milloin Andromeda on korkealla?
  a_ref: SEASONAL_RULES[2].action
  source: src:TAH2
- q: Mikä on Quadrantidien ajankohta?
  a_ref: KNOWLEDGE_TABLES.annual_events[2].date
  source: src:TAH3
- q: Kenelle valosaasteesta ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:TAH2
- q: Mikä on Lyridien ajankohta?
  a_ref: KNOWLEDGE_TABLES.annual_events[3].date
  source: src:TAH3
- q: Mikä on havainnointipaikan leveys- ja pituusaste?
  a_ref: ASSUMPTIONS
  source: src:TAH2
- q: Onko Perseidit vai Geminidit runsaampi?
  a_ref: KNOWLEDGE_TABLES.annual_events
  source: src:TAH3
- q: Milloin pimeä kausi alkaa syksyllä?
  a_ref: SEASONAL_RULES[2].action
  source: src:TAH2
- q: Mitä kuun valossa voi kuvata?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.moon_illumination_limit.action
  source: src:TAH2
- q: Mikä on Geminidien ZHR?
  a_ref: KNOWLEDGE_TABLES.annual_events[1].rate
  source: src:TAH3
- q: Mistä valosaaste tulee?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.light_pollution_bortle.note
  source: src:TAH2
- q: Mikä kamera-preset on taivasnäkymälle?
  a_ref: ASSUMPTIONS
  source: src:TAH2
- q: Milloin yövalokuvaus on mahdotonta?
  a_ref: KNOWLEDGE_TABLES.annual_events[4].note
  source: src:TAH2
- q: Mikä on Lyridien ZHR?
  a_ref: KNOWLEDGE_TABLES.annual_events[3].rate
  source: src:TAH3
- q: Miten valosaastepiikki havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:TAH2
- q: Mikä on erinomainen seeing?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.seeing_arcsec.value
  source: src:TAH2
- q: Miksi pakkasessa kameran akku on ongelma?
  a_ref: SEASONAL_RULES[3].action
  source: src:TAH2
- q: Kuinka pitkä pimeä kausi on joulukuussa 60°N?
  a_ref: KNOWLEDGE_TABLES.annual_events[5].note
  source: src:TAH2
- q: Mihin kohteisiin kuunvalon aikaan ohjataan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.moon_illumination_limit.action
  source: src:TAH2
```

**sources.yaml:**
```yaml
sources:
- id: src:TAH1
  org: Ilmatieteen laitos
  title: Avaruussää ja revontulet
  year: 2025
  url: https://www.ilmatieteenlaitos.fi/revontulet
  supports: Kp-indeksi, revontuliennusteet, aurinkotuuli.
- id: src:TAH2
  org: Tähtitieteellinen yhdistys Ursa
  title: Tähtitaivaan seuranta
  year: 2025
  url: https://www.ursa.fi/
  supports: Seeing, Bortle, havaintokohteet, fenologia.
- id: src:TAH3
  org: International Meteor Organization
  title: Meteor Shower Calendar
  year: 2026
  url: https://www.imo.net/
  supports: Meteorisuihkut, ZHR-arvot, ajankohdat.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Tähtitieteilijä
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Havainnointipaikka: Korvenranta, Kouvola (~60.9°N, 26.7°E)
- Matala valosaaste (Bortle 3-4)
- PTZ-kamera preset 5 (taivasnäkymä) tai erillinen tähtitieteellinen kamera

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| aurora_kp_threshold | 3 | Kp≥3 → revontulimahdollisuus, ilmoita luontokuvaajalle. Kp≥5 → todennäköiset, PTZ pohjoiseen. Kp≥7 → poikkeuksellinen, kaikki ulos. | src:TAH1 |
| seeing_arcsec | <3 arcsekuntia → hyvä, <2 → erinomainen | <2" → erinomainen (planeetat). <3" → hyvä (syväavaruus). >4" → heikko, ei kannata teleskoopilla. Tarkista Meteoblue. | src:TAH1 |
| light_pollution_bortle | 3-4 (maaseututason) | Kouvolan keskustan valonlähde etelässä | src:TAH2 |
| moon_illumination_limit | Kuun valaistus >50% → syväavaruuskohteet heikosti | Ohjaa kuukuvausaiheisiin tai planetaarisiin kohteisiin | src:TAH2 |
| meteor_shower_zenithal_rate | >20 meteoria/h → mainitsemisen arvoinen, >100/h → hälytys (Persidit, Geminidit) | >20/h → maininta käyttäjälle. >100/h (Perseidit 11-13.8) → HÄLYTYS luontokuvaajalle, valmista PTZ. | src:TAH2 |
| iss_pass_brightness_mag | <-2.0 → näkyvä silminnähden, ilmoita käyttäjälle | — | src:TAH3 |

## Tietotaulukot

**annual_events:**

| event | date | rate | source |
| --- | --- | --- | --- |
| Perseidit | 11.-13.8. | 100-150/h | src:TAH3 |
| Geminidit | 13.-14.12. | 120-150/h | src:TAH3 |
| Quadrantidit | 3.-4.1. | 80-120/h | src:TAH3 |
| Lyridit | 22.-23.4. | 15-20/h | src:TAH3 |
| Yötön yö (astron. hämärä ei pääty) | ~25.5.–18.7. (60°N) |  | src:TAH2 |
| Paras pimeä kausi | Joulu-tammikuu |  | src:TAH2 |

## Prosessit

**FLOW_TAHT_01:** aurora_kp_threshold ylittää kynnysarvon (3)
  → Kp≥3 → revontulimahdollisuus, ilmoita luontokuvaajalle. Kp≥5 → todennäköiset, PTZ pohjoiseen. Kp≥7 → poikkeuksellinen, kaikki ulos.
  Tulos: Tilanneraportti

**FLOW_TAHT_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Galaxikausi (Virgo/Coma-klusterit). Revontuliseuranta (equinox-efekti maalis-huhtikuussa
  Tulos: Tarkistuslista

**FLOW_TAHT_03:** Havaittu: Optiikka huurussa
  → Lämmityspanta linssille, tai kuivauspussi kameran viereen
  Tulos: Poikkeamaraportti

**FLOW_TAHT_04:** Säännöllinen heartbeat
  → tahtitieteilija: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Galaxikausi (Virgo/Coma-klusterit). Revontuliseuranta (equinox-efekti maalis-huhtikuussa). Lyridit huhtikuussa. | src:TAH2 |
| **Kesä** | [vko 22-35] Yötön yö: pimeimmilläänkin tähtivalokuvaus hankalaa kesä-heinäkuussa. NLC-pilvet (valaistut yöpilvet) seurannassa. | src:TAH2 |
| **Syksy** | [vko 36-48] Hyvä pimeys palaa syyskuussa. Perseidit elokuussa. Andromeda-galaksi korkealla. | src:TAH2 |
| **Talvi** | [vko 49-13] Paras havaintokausi: pitkät yöt, Orion, Geminidit. Pakkanen → kameran akku ja optiikan huurtuminen huomioitava. | src:TAH2 |

## Virhe- ja vaaratilanteet

### ⚠️ Optiikka huurussa
- **Havaitseminen:** Tähtikuvat sumeita, kastepiste lähellä
- **Toimenpide:** Lämmityspanta linssille, tai kuivauspussi kameran viereen
- **Lähde:** src:TAH2

### ⚠️ Valosaastepiikki
- **Havaitseminen:** Tausta-kirkkaus nousee odottamattomasti
- **Toimenpide:** Tarkista suunta (vältä Kouvolan suuntaa), ilmoita valaistusmestari-agentille
- **Lähde:** src:TAH2

## Epävarmuudet
- Revontuliennusteet ovat luotettavia vain 1-2h ennakolta.
- Meteorimäärien ZHR on ideaaliolosuhdeluku — todellinen havaittava määrä 30-50% tästä.

## Lähteet
- **src:TAH1**: Ilmatieteen laitos — *Avaruussää ja revontulet* (2025) https://www.ilmatieteenlaitos.fi/revontulet
- **src:TAH2**: Tähtitieteellinen yhdistys Ursa — *Tähtitaivaan seuranta* (2025) https://www.ursa.fi/
- **src:TAH3**: International Meteor Organization — *Meteor Shower Calendar* (2026) https://www.imo.net/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä Kp-indeksi tarvitaan revontulille Huhdasjärvellä?**
    → `DECISION_METRICS_AND_THRESHOLDS.aurora_kp_threshold.value` [src:TAH1]
 2. **Milloin Perseidit ovat huipussaan?**
    → `KNOWLEDGE_TABLES.annual_events[0].date` [src:TAH3]
 3. **Mikä on Bortle-luokka Korvenrannassa?**
    → `DECISION_METRICS_AND_THRESHOLDS.light_pollution_bortle.value` [src:TAH2]
 4. **Milloin syväavaruuskuvaus on parhaimmillaan?**
    → `KNOWLEDGE_TABLES.annual_events[5]` [src:TAH2]
 5. **Milloin yötön yö alkaa 60°N?**
    → `KNOWLEDGE_TABLES.annual_events[4].date` [src:TAH2]
 6. **Mikä on ISS:n näkyvyysraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.iss_pass_brightness_mag.value` [src:TAH3]
 7. **Mikä kuun valaistus rajoittaa syväavaruuskuvausta?**
    → `DECISION_METRICS_AND_THRESHOLDS.moon_illumination_limit.value` [src:TAH2]
 8. **Mikä on hyvä seeing-arvo?**
    → `DECISION_METRICS_AND_THRESHOLDS.seeing_arcsec.value` [src:TAH2]
 9. **Milloin revontuliennusteet ovat luotettavimpia?**
    → `UNCERTAINTY_NOTES` [src:TAH1]
10. **Mitä tehdään kun optiikka huurustuu?**
    → `FAILURE_MODES[0].action` [src:TAH2]
11. **Mikä on Geminidien huippu?**
    → `KNOWLEDGE_TABLES.annual_events[1].date` [src:TAH3]
12. **Mikä on Perseidien ZHR?**
    → `KNOWLEDGE_TABLES.annual_events[0].rate` [src:TAH3]
13. **Miksi Kouvolan suuntaa vältetään?**
    → `DECISION_METRICS_AND_THRESHOLDS.light_pollution_bortle.note` [src:TAH2]
14. **Mitä NLC-pilvet ovat?**
    → `SEASONAL_RULES[1].action` [src:TAH2]
15. **Mikä on paras galaksikuvauskausi?**
    → `SEASONAL_RULES[0].action` [src:TAH2]
16. **Milloin equinox-efekti lisää revontulitodennäköisyyttä?**
    → `SEASONAL_RULES[0].action` [src:TAH2]
17. **Mikä on meteorimäärien todellinen havaittavuus vs ZHR?**
    → `UNCERTAINTY_NOTES` [src:TAH3]
18. **Mikä on Kp ≥5 merkitys?**
    → `DECISION_METRICS_AND_THRESHOLDS.aurora_kp_threshold.action` [src:TAH1]
19. **Milloin pimeä kausi on pisin?**
    → `KNOWLEDGE_TABLES.annual_events[5].date` [src:TAH2]
20. **Mitä talvella huomioidaan kameran kanssa?**
    → `SEASONAL_RULES[3].action` [src:TAH2]
21. **Mikä on meteorisuihkun ilmoitusraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.meteor_shower_zenithal_rate.value` [src:TAH3]
22. **Milloin Orion on parhaiten näkyvissä?**
    → `SEASONAL_RULES[3].action` [src:TAH2]
23. **Milloin Andromeda on korkealla?**
    → `SEASONAL_RULES[2].action` [src:TAH2]
24. **Mikä on Quadrantidien ajankohta?**
    → `KNOWLEDGE_TABLES.annual_events[2].date` [src:TAH3]
25. **Kenelle valosaasteesta ilmoitetaan?**
    → `FAILURE_MODES[1].action` [src:TAH2]
26. **Mikä on Lyridien ajankohta?**
    → `KNOWLEDGE_TABLES.annual_events[3].date` [src:TAH3]
27. **Mikä on havainnointipaikan leveys- ja pituusaste?**
    → `ASSUMPTIONS` [src:TAH2]
28. **Onko Perseidit vai Geminidit runsaampi?**
    → `KNOWLEDGE_TABLES.annual_events` [src:TAH3]
29. **Milloin pimeä kausi alkaa syksyllä?**
    → `SEASONAL_RULES[2].action` [src:TAH2]
30. **Mitä kuun valossa voi kuvata?**
    → `DECISION_METRICS_AND_THRESHOLDS.moon_illumination_limit.action` [src:TAH2]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 11
## AGENT 11: Valo- ja varjoanalyytikko
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: valo_varjo
  agent_name: Valo- ja varjoanalyytikko
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Korvenranta 60.9°N, 26.7°E
- Aurinkokulman analyysi mehiläispesien, kasvimaiden, aurinkopaneelien ja asumisen
  optimointiin
- Kytketty valaistusmestari-, hortonomi-, tarhaaja-agentteihin
DECISION_METRICS_AND_THRESHOLDS:
  solar_noon_elevation_summer:
    value: 52.6° (kesäpäivänseisaus, 60.9°N)
    source: src:VAL1
    action: Talvipäivänseisaus 5.8° → varjot pitkät, paneelien kulma 70°. Päivänvalo
      5.7h. Valaistusautomaation kytkentä vko 43.
  solar_noon_elevation_winter:
    value: 5.8° (talvipäivänseisaus)
    source: src:VAL1
  daylength_midsummer_h:
    value: 19.1 h (sis. siviilikrepuskulaari)
    source: src:VAL1
  daylength_midwinter_h:
    value: 5.7 h
    source: src:VAL1
  bee_hive_optimal_morning_sun:
    value: Pesän suuaukko itään-kaakkoon → aamuaurinko lämmittää ja aktivoi
    action: Jos pesä varjossa klo 8-10 kesällä → suosittele siirtoa
    source: src:VAL2
  solar_panel_tilt_optimal:
    value: 40-45° (vuosikeskiarvo 60°N)
    note: Talvi 70°, kesä 15-20°
    source: src:VAL1
    action: 'Varjossa oleva paneeli: -20% tuotto. Yksikin varjostettu kenno → koko
      stringi kärsii. Oksien leikkaus 2x/v (kevät + syksy).'
SEASONAL_RULES:
- season: Kevät
  action: Varjoanalyys kasvimaalle (puut lehdettömiä → tilanne muuttuu). Aurinkopaneelien
    kallistus 30-35°.
  source: src:VAL1
- season: Kesä
  action: Yötön yö-valaistus huomioitava (kameran IR-kytkentä). Pesien ylilämpenemisriski
    suorassa auringossa >35°C.
  source: src:VAL2
- season: Syksy
  action: Varjojen pidentyminen → tarkista aurinkopaneelien tuottoennuste. Kallistus
    50-60°.
  source: src:VAL1
- season: Talvi
  action: Aurinko vain 5.8° korkealla → varjot erittäin pitkiä. Lumipeite heijastaa
    (albedo 0.8-0.9). Paneelikallistus 70°.
  source: src:VAL1
FAILURE_MODES:
- mode: Paneeli varjossa uuden puun takia
  detection: Sähköntuotanto pudonnut >20% edelliseen vuoteen verrattuna
  action: Varjoanalyysi, puun oksien leikkaus tai paneelin siirto
  source: src:VAL1
- mode: Pesien ylilämpö kesällä
  detection: Pesälämpötila >38°C + suora aurinko
  action: Varjostus (lauta pesän päälle), ilmoita tarhaajalle
  source: src:VAL2
PROCESS_FLOWS:
- flow_id: FLOW_VALO_01
  trigger: solar_noon_elevation_summer ylittää kynnysarvon (52.6° (kesäpäivänseisaus,
    60.9°N))
  action: Talvipäivänseisaus 5.8° → varjot pitkät, paneelien kulma 70°. Päivänvalo
    5.7h. Valaistusautomaation kytkentä vko 43.
  output: Tilanneraportti
  source: src:VALO
- flow_id: FLOW_VALO_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: Varjoanalyys kasvimaalle (puut lehdettömiä → tilanne muuttuu). Aurinkopaneelien
    kallistus 30-35°.
  output: Tarkistuslista
  source: src:VALO
- flow_id: FLOW_VALO_03
  trigger: 'Havaittu: Paneeli varjossa uuden puun takia'
  action: Varjoanalyysi, puun oksien leikkaus tai paneelin siirto
  output: Poikkeamaraportti
  source: src:VALO
- flow_id: FLOW_VALO_04
  trigger: Säännöllinen heartbeat
  action: 'valo_varjo: rutiiniarviointi'
  output: Status-raportti
  source: src:VALO
KNOWLEDGE_TABLES:
  solar_calendar:
  - date: 21.3. (kevätpäiväntasaus)
    sunrise: ~06:20
    sunset: ~18:35
    daylength_h: 12.2
    sun_noon_elev: 29.2°
    source: src:VAL1
  - date: 21.6. (kesäpäivänseisaus)
    sunrise: ~03:35
    sunset: ~22:45
    daylength_h: 19.1
    sun_noon_elev: 52.6°
    source: src:VAL1
  - date: 23.9. (syyspäiväntasaus)
    sunrise: ~07:00
    sunset: ~19:15
    daylength_h: 12.2
    sun_noon_elev: 29.2°
    source: src:VAL1
  - date: 21.12. (talvipäivänseisaus)
    sunrise: ~09:25
    sunset: ~15:10
    daylength_h: 5.7
    sun_noon_elev: 5.8°
    source: src:VAL1
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Pilvisyys vaikuttaa merkittävästi todellisiin aurinkoenergiamääriin — laskennalliset
  arvot ovat kirkas-taivas-maksimeja.
SOURCE_REGISTRY:
  sources:
  - id: src:VAL1
    org: Ilmatieteen laitos / USNO
    title: Aurinkolaskelmat 60°N
    year: 2026
    url: https://aa.usno.navy.mil/data/RS_OneYear
    supports: Auringonnousu/-lasku, elevation, päivänpituus.
  - id: src:VAL2
    org: SML / Eva Crane Trust
    title: Mehiläispesien sijoittelu
    year: 2011
    url: null
    identifier: ISBN 978-952-92-9184-4
    supports: Pesien suuntaus ja varjostus.
eval_questions:
- q: Mikä on auringon korkeus keskipäivällä kesäpäivänseisauksena?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.solar_noon_elevation_summer.value
  source: src:VAL1
- q: Mikä on auringon korkeus talvipäivänseisauksena?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.solar_noon_elevation_winter.value
  source: src:VAL1
- q: Kuinka pitkä on päivä keskikesällä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.daylength_midsummer_h.value
  source: src:VAL1
- q: Kuinka pitkä on päivä keskitalvella?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.daylength_midwinter_h.value
  source: src:VAL1
- q: Mihin suuntaan mehiläispesän suuaukko pitäisi osoittaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bee_hive_optimal_morning_sun.value
  source: src:VAL2
- q: Mikä on optimaalinen aurinkopaneelin kallistuskulma?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.value
  source: src:VAL1
- q: Milloin aurinko nousee kesäpäivänseisauksena?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[1].sunrise
  source: src:VAL1
- q: Milloin aurinko laskee talvipäivänseisauksena?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[3].sunset
  source: src:VAL1
- q: Mitä tapahtuu kun pesä ylikuumenee?
  a_ref: FAILURE_MODES[1].action
  source: src:VAL2
- q: Miten paneelin varjostus havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:VAL1
- q: Mikä on talven paneelikallistus?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.note
  source: src:VAL1
- q: Mikä on lumen albedo?
  a_ref: SEASONAL_RULES[3].action
  source: src:VAL1
- q: Milloin varjoanalyysi kasvimaalle tehdään?
  a_ref: SEASONAL_RULES[0].action
  source: src:VAL1
- q: Mikä on pesän ylilämpenemisraja?
  a_ref: FAILURE_MODES[1].detection
  source: src:VAL2
- q: Miten yötön yö vaikuttaa kameroihin?
  a_ref: SEASONAL_RULES[1].action
  source: src:VAL1
- q: Mikä on auringon korkeus kevätpäiväntasauksena?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[0].sun_noon_elev
  source: src:VAL1
- q: Mikä on kesän paneelikallistus?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.note
  source: src:VAL1
- q: Mitä pesälle tehdään suorassa auringossa?
  a_ref: FAILURE_MODES[1].action
  source: src:VAL2
- q: Milloin varjot ovat pisimmillään?
  a_ref: SEASONAL_RULES[3].action
  source: src:VAL1
- q: Mikä on syksyn paneelikallistus?
  a_ref: SEASONAL_RULES[2].action
  source: src:VAL1
- q: Vaikuttaako pilvisyys laskelmiin?
  a_ref: UNCERTAINTY_NOTES
  source: src:VAL1
- q: Mikä on päivän pituus syyspäiväntasauksena?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[2].daylength_h
  source: src:VAL1
- q: Kenelle pesän ylilämmöstä ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:VAL2
- q: Milloin pesä on varjossa ongelmallisesti?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bee_hive_optimal_morning_sun.action
  source: src:VAL2
- q: Mikä on Korvenrannan leveysaste?
  a_ref: ASSUMPTIONS
  source: src:VAL1
- q: Mikä on kevätpäiväntasauksen päivänpituus?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[0].daylength_h
  source: src:VAL1
- q: Miten puiden varjot muuttuvat keväällä?
  a_ref: SEASONAL_RULES[0].action
  source: src:VAL1
- q: Milloin aurinkoenergian tuottoennuste tarkistetaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:VAL1
- q: Mikä on paneelituoton pudotusraja hälytykselle?
  a_ref: FAILURE_MODES[0].detection
  source: src:VAL1
- q: Miten puun varjostus korjataan?
  a_ref: FAILURE_MODES[0].action
  source: src:VAL1
- q: Milloin aurinko nousee talvipäivänseisauksena?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[3].sunrise
  source: src:VAL1
- q: Mikä on kesäauringon korkeuskulma?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[1].sun_noon_elev
  source: src:VAL1
- q: Kenelle varjostusongelmasta ilmoitetaan (paneeli)?
  a_ref: FAILURE_MODES[0].action
  source: src:VAL1
- q: Miten lumi vaikuttaa valon heijastukseen?
  a_ref: SEASONAL_RULES[3].action
  source: src:VAL1
- q: Milloin aurinko laskee kesäpäivänseisauksena?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[1].sunset
  source: src:VAL1
- q: Mikä on päivänvalo-ero kesä- ja talvipäivänseisauksen välillä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:VAL1
- q: Miten pesien ylilämpeneminen estetään?
  a_ref: FAILURE_MODES[1].action
  source: src:VAL2
- q: Onko Korvenrannassa yötöntä yötä?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[1]
  source: src:VAL1
- q: Mikä on auringon korkeuskulma talvella?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[3].sun_noon_elev
  source: src:VAL1
- q: Milloin aurinko nousee kevätpäiväntasauksena?
  a_ref: KNOWLEDGE_TABLES.solar_calendar[0].sunrise
  source: src:VAL1
```

**sources.yaml:**
```yaml
sources:
- id: src:VAL1
  org: Ilmatieteen laitos / USNO
  title: Aurinkolaskelmat 60°N
  year: 2026
  url: https://aa.usno.navy.mil/data/RS_OneYear
  supports: Auringonnousu/-lasku, elevation, päivänpituus.
- id: src:VAL2
  org: SML / Eva Crane Trust
  title: Mehiläispesien sijoittelu
  year: 2011
  url: null
  identifier: ISBN 978-952-92-9184-4
  supports: Pesien suuntaus ja varjostus.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Valo- ja varjoanalyytikko
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Korvenranta 60.9°N, 26.7°E
- Aurinkokulman analyysi mehiläispesien, kasvimaiden, aurinkopaneelien ja asumisen optimointiin
- Kytketty valaistusmestari-, hortonomi-, tarhaaja-agentteihin

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| solar_noon_elevation_summer | 52.6° (kesäpäivänseisaus, 60.9°N) | Talvipäivänseisaus 5.8° → varjot pitkät, paneelien kulma 70°. Päivänvalo 5.7h. Valaistusautomaation kytkentä vko 43. | src:VAL1 |
| solar_noon_elevation_winter | 5.8° (talvipäivänseisaus) | — | src:VAL1 |
| daylength_midsummer_h | 19.1 h (sis. siviilikrepuskulaari) | — | src:VAL1 |
| daylength_midwinter_h | 5.7 h | — | src:VAL1 |
| bee_hive_optimal_morning_sun | Pesän suuaukko itään-kaakkoon → aamuaurinko lämmittää ja aktivoi | Jos pesä varjossa klo 8-10 kesällä → suosittele siirtoa | src:VAL2 |
| solar_panel_tilt_optimal | 40-45° (vuosikeskiarvo 60°N) | Varjossa oleva paneeli: -20% tuotto. Yksikin varjostettu kenno → koko stringi kärsii. Oksien leikkaus 2x/v (kevät + syksy). | src:VAL1 |

## Tietotaulukot

**solar_calendar:**

| date | sunrise | sunset | daylength_h | sun_noon_elev | source |
| --- | --- | --- | --- | --- | --- |
| 21.3. (kevätpäiväntasaus) | ~06:20 | ~18:35 | 12.2 | 29.2° | src:VAL1 |
| 21.6. (kesäpäivänseisaus) | ~03:35 | ~22:45 | 19.1 | 52.6° | src:VAL1 |
| 23.9. (syyspäiväntasaus) | ~07:00 | ~19:15 | 12.2 | 29.2° | src:VAL1 |
| 21.12. (talvipäivänseisaus) | ~09:25 | ~15:10 | 5.7 | 5.8° | src:VAL1 |

## Prosessit

**FLOW_VALO_01:** solar_noon_elevation_summer ylittää kynnysarvon (52.6° (kesäpäivänseisaus, 60.9°N))
  → Talvipäivänseisaus 5.8° → varjot pitkät, paneelien kulma 70°. Päivänvalo 5.7h. Valaistusautomaation kytkentä vko 43.
  Tulos: Tilanneraportti

**FLOW_VALO_02:** Kausi vaihtuu: Kevät
  → Varjoanalyys kasvimaalle (puut lehdettömiä → tilanne muuttuu). Aurinkopaneelien kallistus 30-35°.
  Tulos: Tarkistuslista

**FLOW_VALO_03:** Havaittu: Paneeli varjossa uuden puun takia
  → Varjoanalyysi, puun oksien leikkaus tai paneelin siirto
  Tulos: Poikkeamaraportti

**FLOW_VALO_04:** Säännöllinen heartbeat
  → valo_varjo: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | Varjoanalyys kasvimaalle (puut lehdettömiä → tilanne muuttuu). Aurinkopaneelien kallistus 30-35°. | src:VAL1 |
| **Kesä** | Yötön yö-valaistus huomioitava (kameran IR-kytkentä). Pesien ylilämpenemisriski suorassa auringossa >35°C. | src:VAL2 |
| **Syksy** | Varjojen pidentyminen → tarkista aurinkopaneelien tuottoennuste. Kallistus 50-60°. | src:VAL1 |
| **Talvi** | Aurinko vain 5.8° korkealla → varjot erittäin pitkiä. Lumipeite heijastaa (albedo 0.8-0.9). Paneelikallistus 70°. | src:VAL1 |

## Virhe- ja vaaratilanteet

### ⚠️ Paneeli varjossa uuden puun takia
- **Havaitseminen:** Sähköntuotanto pudonnut >20% edelliseen vuoteen verrattuna
- **Toimenpide:** Varjoanalyysi, puun oksien leikkaus tai paneelin siirto
- **Lähde:** src:VAL1

### ⚠️ Pesien ylilämpö kesällä
- **Havaitseminen:** Pesälämpötila >38°C + suora aurinko
- **Toimenpide:** Varjostus (lauta pesän päälle), ilmoita tarhaajalle
- **Lähde:** src:VAL2

## Epävarmuudet
- Pilvisyys vaikuttaa merkittävästi todellisiin aurinkoenergiamääriin — laskennalliset arvot ovat kirkas-taivas-maksimeja.

## Lähteet
- **src:VAL1**: Ilmatieteen laitos / USNO — *Aurinkolaskelmat 60°N* (2026) https://aa.usno.navy.mil/data/RS_OneYear
- **src:VAL2**: SML / Eva Crane Trust — *Mehiläispesien sijoittelu* (2011) —

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on auringon korkeus keskipäivällä kesäpäivänseisauksena?**
    → `DECISION_METRICS_AND_THRESHOLDS.solar_noon_elevation_summer.value` [src:VAL1]
 2. **Mikä on auringon korkeus talvipäivänseisauksena?**
    → `DECISION_METRICS_AND_THRESHOLDS.solar_noon_elevation_winter.value` [src:VAL1]
 3. **Kuinka pitkä on päivä keskikesällä?**
    → `DECISION_METRICS_AND_THRESHOLDS.daylength_midsummer_h.value` [src:VAL1]
 4. **Kuinka pitkä on päivä keskitalvella?**
    → `DECISION_METRICS_AND_THRESHOLDS.daylength_midwinter_h.value` [src:VAL1]
 5. **Mihin suuntaan mehiläispesän suuaukko pitäisi osoittaa?**
    → `DECISION_METRICS_AND_THRESHOLDS.bee_hive_optimal_morning_sun.value` [src:VAL2]
 6. **Mikä on optimaalinen aurinkopaneelin kallistuskulma?**
    → `DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.value` [src:VAL1]
 7. **Milloin aurinko nousee kesäpäivänseisauksena?**
    → `KNOWLEDGE_TABLES.solar_calendar[1].sunrise` [src:VAL1]
 8. **Milloin aurinko laskee talvipäivänseisauksena?**
    → `KNOWLEDGE_TABLES.solar_calendar[3].sunset` [src:VAL1]
 9. **Mitä tapahtuu kun pesä ylikuumenee?**
    → `FAILURE_MODES[1].action` [src:VAL2]
10. **Miten paneelin varjostus havaitaan?**
    → `FAILURE_MODES[0].detection` [src:VAL1]
11. **Mikä on talven paneelikallistus?**
    → `DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.note` [src:VAL1]
12. **Mikä on lumen albedo?**
    → `SEASONAL_RULES[3].action` [src:VAL1]
13. **Milloin varjoanalyysi kasvimaalle tehdään?**
    → `SEASONAL_RULES[0].action` [src:VAL1]
14. **Mikä on pesän ylilämpenemisraja?**
    → `FAILURE_MODES[1].detection` [src:VAL2]
15. **Miten yötön yö vaikuttaa kameroihin?**
    → `SEASONAL_RULES[1].action` [src:VAL1]
16. **Mikä on auringon korkeus kevätpäiväntasauksena?**
    → `KNOWLEDGE_TABLES.solar_calendar[0].sun_noon_elev` [src:VAL1]
17. **Mikä on kesän paneelikallistus?**
    → `DECISION_METRICS_AND_THRESHOLDS.solar_panel_tilt_optimal.note` [src:VAL1]
18. **Mitä pesälle tehdään suorassa auringossa?**
    → `FAILURE_MODES[1].action` [src:VAL2]
19. **Milloin varjot ovat pisimmillään?**
    → `SEASONAL_RULES[3].action` [src:VAL1]
20. **Mikä on syksyn paneelikallistus?**
    → `SEASONAL_RULES[2].action` [src:VAL1]
21. **Vaikuttaako pilvisyys laskelmiin?**
    → `UNCERTAINTY_NOTES` [src:VAL1]
22. **Mikä on päivän pituus syyspäiväntasauksena?**
    → `KNOWLEDGE_TABLES.solar_calendar[2].daylength_h` [src:VAL1]
23. **Kenelle pesän ylilämmöstä ilmoitetaan?**
    → `FAILURE_MODES[1].action` [src:VAL2]
24. **Milloin pesä on varjossa ongelmallisesti?**
    → `DECISION_METRICS_AND_THRESHOLDS.bee_hive_optimal_morning_sun.action` [src:VAL2]
25. **Mikä on Korvenrannan leveysaste?**
    → `ASSUMPTIONS` [src:VAL1]
26. **Mikä on kevätpäiväntasauksen päivänpituus?**
    → `KNOWLEDGE_TABLES.solar_calendar[0].daylength_h` [src:VAL1]
27. **Miten puiden varjot muuttuvat keväällä?**
    → `SEASONAL_RULES[0].action` [src:VAL1]
28. **Milloin aurinkoenergian tuottoennuste tarkistetaan?**
    → `SEASONAL_RULES[2].action` [src:VAL1]
29. **Mikä on paneelituoton pudotusraja hälytykselle?**
    → `FAILURE_MODES[0].detection` [src:VAL1]
30. **Miten puun varjostus korjataan?**
    → `FAILURE_MODES[0].action` [src:VAL1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 12
## AGENT 12: Tarhaaja (Päämehiläishoitaja)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: tarhaaja
  agent_name: Tarhaaja (Päämehiläishoitaja)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- 202 yhdyskuntaa (35 tarhaa), useita tarhoja (Helsinki, Kouvola, Huhdasjärvi)
- JKH Service Y-tunnus 2828492-2
- Vuosituotanto ~10 000 kg hunajaa
- 'Hoitomalli: Langstroth-kehykset'
DECISION_METRICS_AND_THRESHOLDS:
  varroa_threshold_per_100:
    value: 3
    action: '>3 punkkia/100 mehiläistä → kemiallinen hoito välittömästi'
    source: src:TAR1
  colony_weight_spring_min_kg:
    value: 15
    action: Alle 15 kg keväällä → hätäruokinta sokeriliuoksella (1:1)
    source: src:TAR2
  winter_cluster_core_temp_c:
    value: 20
    action: 'Ydinlämpö <20°C → KRIITTINEN: yhdyskunta heikkenee'
    source: src:TAR2
  brood_frame_count_spring_min:
    value: 3
    action: Alle 3 sikiökehystä huhtikuussa → pesä heikko, yhdistämisharkinta
    source: src:TAR2
  honey_moisture_max_pct:
    value: 18.0
    action: Yli 18% → ei linkota, anna kuivua kannessa
    source: src:TAR2
  swarming_risk_indicators:
    value: Emokopat, pesä ahdas, nuoret mehiläiset kaarella
    action: Jaa pesä tai poista emokopat 7pv välein
    source: src:TAR2
  feeding_autumn_sugar_kg:
    value: 15-20 kg sokeria / pesä (vyöhyke II-III)
    source: src:TAR2
  deg_day_threshold_feeding_start:
    value: Alle 1000 °Cvr → aloita syysruokinta viimeistään vko 34
    source: src:TAR2
SEASONAL_RULES:
- season: Kevät
  action: Kevättarkastus kun vrk-T >10°C kahtena peräkkäisenä pv. Ruokavarastojen
    tarkistus. Emon haku. Laajennukset.
  source: src:TAR2
- season: Kesä
  action: Korotukset satomaiden mukaan. Parveiluntarkistus 7pv välein. Hunajan linkoaminen
    RH<18%.
  source: src:TAR2
- season: Syksy
  action: Varroa-hoito heti viimeisen linkoamisen jälkeen. Syysruokinta 15-20 kg sokeria/pesä.
    Talvipakkaus lokakuussa.
  source: src:TAR2
- season: Talvi
  action: EI avata pesiä. Painonseuranta (heiluri/vaaka). Jos paino putoaa >0.5 kg/vko
    marras-helmikuussa → harkitse hätäruokintaa.
  source: src:TAR2
FAILURE_MODES:
- mode: Emottomaksi jäänyt pesä
  detection: Ei sikiötä, hajakuviollista munintaa, aggressiivisuus
  action: Yhdistä lehtipaperilla naapuripesään TAI anna uusi emo
  source: src:TAR2
- mode: AFB-epäily
  detection: Itiöiset, uponneet kannet, tikkulanka, haju
  action: ÄLÄ siirrä kehyksiä! Ilmoita Ruokavirastolle. Eristä pesä.
  source: src:TAR1
- mode: Talvikuolema
  detection: Keväällä tyhjä pesä, mehiläiset kuolleina pohjalla
  action: Dokumentoi, tarkista varroa-jäämät, älä kierrätä kehyksiä ennen tautitarkistusta
  source: src:TAR2
PROCESS_FLOWS:
  annual_cycle:
    steps:
    - 'Maalis: kevättarkastus (ruokavarastot, emo, sikiö)'
    - 'Huhti-touko: laajentaminen, korotukset, emontarkkailu'
    - 'Kesä-heinä: hunajalinkoaminen, parveilunhallinta'
    - 'Elo: viimeinen lintaus, varroa-hoito, syysruokinta alkaa'
    - 'Syys: ruokinta valmis viim. syyskuun loppu, talvipakkaus'
    - 'Loka-maalis: talvilevo, painonseuranta, ei avata pesiä'
KNOWLEDGE_TABLES:
  apiaries:
  - location: Huhdasjärvi
    hive_count: ~50
    zone: II-III
    main_flora: Maitohorsma, vadelma, apilat
    source: src:TAR2
  - location: Kouvolan alue
    hive_count: ~100
    zone: II
    main_flora: Rypsi, vadelma, hedelmäpuut
    source: src:TAR2
  - location: Helsinki/Itäkeskus
    hive_count: ~50
    zone: I
    main_flora: Puistokasvit, lehmukset, apilat
    source: src:TAR2
  - location: Muut sijainnit
    hive_count: ~100
    zone: I-III
    main_flora: Vaihteleva
    source: src:TAR2
COMPLIANCE_AND_LEGAL:
  registration: Eläintenpitäjäksi rekisteröityminen Ruokavirastoon pakollinen [src:TAR1]
  afb_notification: AFB on valvottava eläintauti — aina ilmoitus Ruokavirastolle [src:TAR1]
  honey_direct_sale: Suoramyynti kuluttajalle max 2500 kg/vuosi ilman huoneistoilmoitusta
    [src:TAR3]
  vat: Hunaja ALV 13.5% (elintarvike, 1.1.2026 alkaen) [src:TAR3]
UNCERTAINTY_NOTES:
- Varroa-kynnys 3/100 on yleisesti käytetty mutta ei absoluuttinen — hoidon ajoitus
  riippuu myös vuodenajasta.
- Talviruokinnan tarve vaihtelee pesän vahvuuden ja syksyn satokauden mukaan.
SOURCE_REGISTRY:
  sources:
  - id: src:TAR1
    org: Ruokavirasto
    title: Mehiläisten taudit ja pito
    year: 2025
    url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
    supports: AFB, varroa, rekisteröinti.
  - id: src:TAR2
    org: Suomen Mehiläishoitajain Liitto
    title: Mehiläishoitoa käytännössä
    year: 2011
    url: null
    identifier: ISBN 978-952-92-9184-4
    supports: Pesänhoitosykli, varroa, ruokinta, parveilunhallinta.
  - id: src:TAR3
    org: Verohallinto / Ruokavirasto
    title: ALV ja alkutuotanto
    year: 2026
    url: https://www.vero.fi/
    supports: Hunajan ALV, suoramyyntiraja.
eval_questions:
- q: Mikä on varroa-hoitokynnys?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.varroa_threshold_per_100.value
  source: src:TAR1
- q: Mikä on pesän minimipaino keväällä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.colony_weight_spring_min_kg.value
  source: src:TAR2
- q: Mikä on talvipallon ytimen kriittinen lämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_temp_c.value
  source: src:TAR2
- q: Mikä on hunajan kosteusyläraja linkoamiselle?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.honey_moisture_max_pct.value
  source: src:TAR2
- q: Kuinka paljon sokeria syysruokintaan per pesä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.feeding_autumn_sugar_kg.value
  source: src:TAR2
- q: Mitkä ovat parveilun merkit?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.swarming_risk_indicators.value
  source: src:TAR2
- q: Milloin varroa-hoito tehdään?
  a_ref: SEASONAL_RULES[2].action
  source: src:TAR2
- q: Mitä tehdään AFB-epäilyssä?
  a_ref: FAILURE_MODES[1].action
  source: src:TAR1
- q: Milloin kevättarkastus tehdään?
  a_ref: SEASONAL_RULES[0].action
  source: src:TAR2
- q: Mikä on syysruokinnan viimeinen ajankohta?
  a_ref: SEASONAL_RULES[2].action
  source: src:TAR2
- q: Miten talvella seurataan pesää?
  a_ref: SEASONAL_RULES[3].action
  source: src:TAR2
- q: Mikä on painonpudotuksen hälytysraja talvella?
  a_ref: SEASONAL_RULES[3].action
  source: src:TAR2
- q: Paljonko pesillä on Huhdasjärvellä?
  a_ref: KNOWLEDGE_TABLES.apiaries[0].hive_count
  source: src:TAR2
- q: Mikä on ALV hunajalle?
  a_ref: COMPLIANCE_AND_LEGAL.vat
  source: src:TAR3
- q: Mikä on suoramyynnin kiloraja?
  a_ref: COMPLIANCE_AND_LEGAL.honey_direct_sale
  source: src:TAR3
- q: Miten emoton pesä tunnistetaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:TAR2
- q: Mitä tehdään emottomalle pesälle?
  a_ref: FAILURE_MODES[0].action
  source: src:TAR2
- q: Mikä °Cvr-raja laukaisee ruokinnan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.deg_day_threshold_feeding_start.value
  source: src:TAR2
- q: Mikä on sikiökehysten minimiraja keväällä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.brood_frame_count_spring_min.value
  source: src:TAR2
- q: Onko AFB ilmoitettava tauti?
  a_ref: COMPLIANCE_AND_LEGAL.afb_notification
  source: src:TAR1
- q: Miten talvikuolema dokumentoidaan?
  a_ref: FAILURE_MODES[2].action
  source: src:TAR2
- q: Mikä on parveiluntarkistusväli?
  a_ref: SEASONAL_RULES[1].action
  source: src:TAR2
- q: Onko rekisteröityminen pakollista?
  a_ref: COMPLIANCE_AND_LEGAL.registration
  source: src:TAR1
- q: Miten hätäruokinta tehdään keväällä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.colony_weight_spring_min_kg.action
  source: src:TAR2
- q: Milloin pesiä ei saa avata?
  a_ref: SEASONAL_RULES[3].action
  source: src:TAR2
- q: Mitä kehyksille tehdään talvikuoleman jälkeen?
  a_ref: FAILURE_MODES[2].action
  source: src:TAR2
- q: Miten AFB tunnistetaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:TAR1
- q: Mikä on Helsingissä pääkasvilajisto?
  a_ref: KNOWLEDGE_TABLES.apiaries[2].main_flora
  source: src:TAR2
- q: Paljonko pesiä on yhteensä?
  a_ref: ASSUMPTIONS
  source: src:TAR2
- q: Miten heikko pesä vahvistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.brood_frame_count_spring_min.action
  source: src:TAR2
- q: Mikä on vuosituotannon arvio?
  a_ref: ASSUMPTIONS
  source: src:TAR2
- q: Milloin talvipakkaus tehdään?
  a_ref: SEASONAL_RULES[2].action
  source: src:TAR2
- q: Miten syysruokinta ajoitetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.deg_day_threshold_feeding_start.value
  source: src:TAR2
- q: Mikä on linkoamiskausi?
  a_ref: PROCESS_FLOWS.annual_cycle.steps
  source: src:TAR2
- q: Saako AFB-kehyksiä siirtää?
  a_ref: FAILURE_MODES[1].action
  source: src:TAR1
- q: Milloin emontarkkailu on kriittistä?
  a_ref: PROCESS_FLOWS.annual_cycle.steps
  source: src:TAR2
- q: Mikä on Huhdasjärven pääkasvilajisto?
  a_ref: KNOWLEDGE_TABLES.apiaries[0].main_flora
  source: src:TAR2
- q: Onko varroa-kynnys absoluuttinen?
  a_ref: UNCERTAINTY_NOTES
  source: src:TAR1
- q: Mikä on hoitomalli?
  a_ref: ASSUMPTIONS
  source: src:TAR2
- q: Mikä on Huhdasjärven vyöhyke?
  a_ref: KNOWLEDGE_TABLES.apiaries[0].zone
  source: src:TAR2
```

**sources.yaml:**
```yaml
sources:
- id: src:TAR1
  org: Ruokavirasto
  title: Mehiläisten taudit ja pito
  year: 2025
  url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
  supports: AFB, varroa, rekisteröinti.
- id: src:TAR2
  org: Suomen Mehiläishoitajain Liitto
  title: Mehiläishoitoa käytännössä
  year: 2011
  url: null
  identifier: ISBN 978-952-92-9184-4
  supports: Pesänhoitosykli, varroa, ruokinta, parveilunhallinta.
- id: src:TAR3
  org: Verohallinto / Ruokavirasto
  title: ALV ja alkutuotanto
  year: 2026
  url: https://www.vero.fi/
  supports: Hunajan ALV, suoramyyntiraja.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Tarhaaja (Päämehiläishoitaja)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- 202 yhdyskuntaa (35 tarhaa), useita tarhoja (Helsinki, Kouvola, Huhdasjärvi)
- JKH Service Y-tunnus 2828492-2
- Vuosituotanto ~10 000 kg hunajaa
- Hoitomalli: Langstroth-kehykset

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| varroa_threshold_per_100 | 3 | >3 punkkia/100 mehiläistä → kemiallinen hoito välittömästi | src:TAR1 |
| colony_weight_spring_min_kg | 15 | Alle 15 kg keväällä → hätäruokinta sokeriliuoksella (1:1) | src:TAR2 |
| winter_cluster_core_temp_c | 20 | Ydinlämpö <20°C → KRIITTINEN: yhdyskunta heikkenee | src:TAR2 |
| brood_frame_count_spring_min | 3 | Alle 3 sikiökehystä huhtikuussa → pesä heikko, yhdistämisharkinta | src:TAR2 |
| honey_moisture_max_pct | 18.0 | Yli 18% → ei linkota, anna kuivua kannessa | src:TAR2 |
| swarming_risk_indicators | Emokopat, pesä ahdas, nuoret mehiläiset kaarella | Jaa pesä tai poista emokopat 7pv välein | src:TAR2 |
| feeding_autumn_sugar_kg | 15-20 kg sokeria / pesä (vyöhyke II-III) | — | src:TAR2 |
| deg_day_threshold_feeding_start | Alle 1000 °Cvr → aloita syysruokinta viimeistään vko 34 | — | src:TAR2 |

## Tietotaulukot

**apiaries:**

| location | hive_count | zone | main_flora | source |
| --- | --- | --- | --- | --- |
| Huhdasjärvi | ~50 | II-III | Maitohorsma, vadelma, apilat | src:TAR2 |
| Kouvolan alue | ~100 | II | Rypsi, vadelma, hedelmäpuut | src:TAR2 |
| Helsinki/Itäkeskus | ~50 | I | Puistokasvit, lehmukset, apilat | src:TAR2 |
| Muut sijainnit | ~100 | I-III | Vaihteleva | src:TAR2 |

## Prosessit

**annual_cycle:**
  Maalis: kevättarkastus (ruokavarastot, emo, sikiö)
  Huhti-touko: laajentaminen, korotukset, emontarkkailu
  Kesä-heinä: hunajalinkoaminen, parveilunhallinta
  Elo: viimeinen lintaus, varroa-hoito, syysruokinta alkaa
  Syys: ruokinta valmis viim. syyskuun loppu, talvipakkaus
  Loka-maalis: talvilevo, painonseuranta, ei avata pesiä

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | Kevättarkastus kun vrk-T >10°C kahtena peräkkäisenä pv. Ruokavarastojen tarkistus. Emon haku. Laajennukset. | src:TAR2 |
| **Kesä** | Korotukset satomaiden mukaan. Parveiluntarkistus 7pv välein. Hunajan linkoaminen RH<18%. | src:TAR2 |
| **Syksy** | Varroa-hoito heti viimeisen linkoamisen jälkeen. Syysruokinta 15-20 kg sokeria/pesä. Talvipakkaus lokakuussa. | src:TAR2 |
| **Talvi** | EI avata pesiä. Painonseuranta (heiluri/vaaka). Jos paino putoaa >0.5 kg/vko marras-helmikuussa → harkitse hätäruokintaa. | src:TAR2 |

## Virhe- ja vaaratilanteet

### ⚠️ Emottomaksi jäänyt pesä
- **Havaitseminen:** Ei sikiötä, hajakuviollista munintaa, aggressiivisuus
- **Toimenpide:** Yhdistä lehtipaperilla naapuripesään TAI anna uusi emo
- **Lähde:** src:TAR2

### ⚠️ AFB-epäily
- **Havaitseminen:** Itiöiset, uponneet kannet, tikkulanka, haju
- **Toimenpide:** ÄLÄ siirrä kehyksiä! Ilmoita Ruokavirastolle. Eristä pesä.
- **Lähde:** src:TAR1

### ⚠️ Talvikuolema
- **Havaitseminen:** Keväällä tyhjä pesä, mehiläiset kuolleina pohjalla
- **Toimenpide:** Dokumentoi, tarkista varroa-jäämät, älä kierrätä kehyksiä ennen tautitarkistusta
- **Lähde:** src:TAR2

## Lait ja vaatimukset
- **registration:** Eläintenpitäjäksi rekisteröityminen Ruokavirastoon pakollinen [src:TAR1]
- **afb_notification:** AFB on valvottava eläintauti — aina ilmoitus Ruokavirastolle [src:TAR1]
- **honey_direct_sale:** Suoramyynti kuluttajalle max 2500 kg/vuosi ilman huoneistoilmoitusta [src:TAR3]
- **vat:** Hunaja ALV 13.5% (elintarvike, 1.1.2026 alkaen) [src:TAR3]

## Epävarmuudet
- Varroa-kynnys 3/100 on yleisesti käytetty mutta ei absoluuttinen — hoidon ajoitus riippuu myös vuodenajasta.
- Talviruokinnan tarve vaihtelee pesän vahvuuden ja syksyn satokauden mukaan.

## Lähteet
- **src:TAR1**: Ruokavirasto — *Mehiläisten taudit ja pito* (2025) https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
- **src:TAR2**: Suomen Mehiläishoitajain Liitto — *Mehiläishoitoa käytännössä* (2011) —
- **src:TAR3**: Verohallinto / Ruokavirasto — *ALV ja alkutuotanto* (2026) https://www.vero.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on varroa-hoitokynnys?**
    → `DECISION_METRICS_AND_THRESHOLDS.varroa_threshold_per_100.value` [src:TAR1]
 2. **Mikä on pesän minimipaino keväällä?**
    → `DECISION_METRICS_AND_THRESHOLDS.colony_weight_spring_min_kg.value` [src:TAR2]
 3. **Mikä on talvipallon ytimen kriittinen lämpötila?**
    → `DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_temp_c.value` [src:TAR2]
 4. **Mikä on hunajan kosteusyläraja linkoamiselle?**
    → `DECISION_METRICS_AND_THRESHOLDS.honey_moisture_max_pct.value` [src:TAR2]
 5. **Kuinka paljon sokeria syysruokintaan per pesä?**
    → `DECISION_METRICS_AND_THRESHOLDS.feeding_autumn_sugar_kg.value` [src:TAR2]
 6. **Mitkä ovat parveilun merkit?**
    → `DECISION_METRICS_AND_THRESHOLDS.swarming_risk_indicators.value` [src:TAR2]
 7. **Milloin varroa-hoito tehdään?**
    → `SEASONAL_RULES[2].action` [src:TAR2]
 8. **Mitä tehdään AFB-epäilyssä?**
    → `FAILURE_MODES[1].action` [src:TAR1]
 9. **Milloin kevättarkastus tehdään?**
    → `SEASONAL_RULES[0].action` [src:TAR2]
10. **Mikä on syysruokinnan viimeinen ajankohta?**
    → `SEASONAL_RULES[2].action` [src:TAR2]
11. **Miten talvella seurataan pesää?**
    → `SEASONAL_RULES[3].action` [src:TAR2]
12. **Mikä on painonpudotuksen hälytysraja talvella?**
    → `SEASONAL_RULES[3].action` [src:TAR2]
13. **Paljonko pesillä on Huhdasjärvellä?**
    → `KNOWLEDGE_TABLES.apiaries[0].hive_count` [src:TAR2]
14. **Mikä on ALV hunajalle?**
    → `COMPLIANCE_AND_LEGAL.vat` [src:TAR3]
15. **Mikä on suoramyynnin kiloraja?**
    → `COMPLIANCE_AND_LEGAL.honey_direct_sale` [src:TAR3]
16. **Miten emoton pesä tunnistetaan?**
    → `FAILURE_MODES[0].detection` [src:TAR2]
17. **Mitä tehdään emottomalle pesälle?**
    → `FAILURE_MODES[0].action` [src:TAR2]
18. **Mikä °Cvr-raja laukaisee ruokinnan?**
    → `DECISION_METRICS_AND_THRESHOLDS.deg_day_threshold_feeding_start.value` [src:TAR2]
19. **Mikä on sikiökehysten minimiraja keväällä?**
    → `DECISION_METRICS_AND_THRESHOLDS.brood_frame_count_spring_min.value` [src:TAR2]
20. **Onko AFB ilmoitettava tauti?**
    → `COMPLIANCE_AND_LEGAL.afb_notification` [src:TAR1]
21. **Miten talvikuolema dokumentoidaan?**
    → `FAILURE_MODES[2].action` [src:TAR2]
22. **Mikä on parveiluntarkistusväli?**
    → `SEASONAL_RULES[1].action` [src:TAR2]
23. **Onko rekisteröityminen pakollista?**
    → `COMPLIANCE_AND_LEGAL.registration` [src:TAR1]
24. **Miten hätäruokinta tehdään keväällä?**
    → `DECISION_METRICS_AND_THRESHOLDS.colony_weight_spring_min_kg.action` [src:TAR2]
25. **Milloin pesiä ei saa avata?**
    → `SEASONAL_RULES[3].action` [src:TAR2]
26. **Mitä kehyksille tehdään talvikuoleman jälkeen?**
    → `FAILURE_MODES[2].action` [src:TAR2]
27. **Miten AFB tunnistetaan?**
    → `FAILURE_MODES[1].detection` [src:TAR1]
28. **Mikä on Helsingissä pääkasvilajisto?**
    → `KNOWLEDGE_TABLES.apiaries[2].main_flora` [src:TAR2]
29. **Paljonko pesiä on yhteensä?**
    → `ASSUMPTIONS` [src:TAR2]
30. **Miten heikko pesä vahvistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.brood_frame_count_spring_min.action` [src:TAR2]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 13
## AGENT 13: Lentosää-analyytikko
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: lentosaa
  agent_name: Lentosää-analyytikko
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Mehiläisten lentoaktiivisuuden sää-arviointi
- Yhdistää meteorologin datan mehiläishoidon päätöksiin
- Korvenranta + muut tarha-alueet
DECISION_METRICS_AND_THRESHOLDS:
  min_flight_temp_c:
    value: 10
    note: Satunnaisia lentoja >10°C, normaali keräily >13°C, optimaalinen >15°C
    source: src:LEN1
    action: T<10°C → EI lentoa, ei tarkastuskäyntiä. 10-13°C → vähäinen aktiivisuus.
      >15°C optimaalinen. Ilmoita tarhaajalle tarkistusikkunat.
  max_wind_speed_flight_ms:
    value: 8
    action: '>8 m/s → mehiläisten aktiivisuus -50%. >12 m/s → ei lentoa. Tuuleton
      + aurinko + T>15°C = täysaktiivisuus.'
    source: src:LEN1
  rain_flight_stop:
    value: Sade >0.5 mm/h → mehiläiset eivät lennä
    source: src:LEN1
    action: '>0.5 mm/h → ei lentoa. Sade >3 pv kesä-heinäkuussa → tarkista ruokavarasto
      (kulutus ilman tuontia ~0.5 kg/pv).'
  optimal_flying_conditions:
    value: T >15°C, tuuli <5 m/s, ei sadetta, pilvisyys <6/8
    source: src:LSA1
  nectar_secretion_humidity:
    value: Ilmankosteus 50-80% → meden eritys optimaalista
    action: Alle 40% → kasvit eivät eritä, ilmoita nektari-informaatikolle
    source: src:LSA2
  inspection_weather:
    value: T >15°C, ei sadetta, tuuli <5 m/s → sopiva pesäntarkistukselle
    source: src:LSA1
SEASONAL_RULES:
- season: Kevät
  action: Ensimmäiset lentopäivät (>10°C) kriittisiä pesän kunnon indikaattorina.
    Puhdistuslento.
  source: src:LSA1
- season: Kesä
  action: Optimaalinen keräilykausi. Helle >30°C → mehiläiset tuulettavat, lisää vettä
    tarhan lähelle.
  source: src:LSA1
- season: Syksy
  action: Lentopäivät vähenevät. Viimeiset +10°C päivät → oksaalihappohoito (pesimätön
    kausi alkaa).
  source: src:LSA1
- season: Talvi
  action: Ei lentotoimintaa. Poikkeukselliset +5°C päivät → mehiläisten ulostuskierto
    (positiivinen merkki).
  source: src:LSA1
FAILURE_MODES:
- mode: Pitkä sadejakso keräilykaudella
  detection: Sade >3 vrk peräkkäin kesä-heinäkuussa
  action: Tarkkaile pesien ruokavarastoja, harkitse hätäruokintaa
  source: src:LSA1
- mode: Yllättävä pakkasvuorokausi keväällä
  detection: T <0°C huhtikuussa lentokauden jälkeen
  action: 'Ilmoita tarhaajalle: pesää ei saa avata, riskinä sikiön kylmettyminen'
  source: src:LSA1
PROCESS_FLOWS:
  daily_assessment:
    steps:
    - 1. Hae sääennuste meteorologi-agentilta (3h ja 24h)
    - 2. Laske lento-oloindeksi (T, tuuli, sade, pilvisyys)
    - 3. Jos indeksi 'hyvä' → ilmoita tarhaajalle optimaalisista toimintaajoista
    - 4. Jos indeksi 'huono' >3pv → varoita tarhaajaa (pesät eivät keräile, ruokavarat
      laskevat)
    - '5. Ilmoita parveiluvahdille: hyvät olosuhteet = korkea parveilriski'
KNOWLEDGE_TABLES:
- table_id: TBL_LENT_01
  title: Lentosää-analyytikko — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: min_flight_temp_c
    value: '10'
    action: T<10°C → EI lentoa, ei tarkastuskäyntiä. 10-13°C → vähäinen aktiivisuus.
      >15°C o
  - metric: max_wind_speed_flight_ms
    value: '8'
    action: '>8 m/s → mehiläisten aktiivisuus -50%. >12 m/s → ei lentoa. Tuuleton
      + aurinko +'
  - metric: rain_flight_stop
    value: Sade >0.5 mm/h → mehiläiset eivät lennä
    action: '>0.5 mm/h → ei lentoa. Sade >3 pv kesä-heinäkuussa → tarkista ruokavarasto
      (kulu'
  - metric: optimal_flying_conditions
    value: T >15°C, tuuli <5 m/s, ei sadetta, pilvisyys <6/8
    action: ''
  - metric: nectar_secretion_humidity
    value: Ilmankosteus 50-80% → meden eritys optimaalista
    action: Alle 40% → kasvit eivät eritä, ilmoita nektari-informaatikolle
  - metric: inspection_weather
    value: T >15°C, ei sadetta, tuuli <5 m/s → sopiva pesäntarkistukselle
    action: ''
  source: src:LENT
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Mikroilmasto tarhan ympäristössä voi poiketa yleisennusteesta ±2°C.
- Lento-oloindeksi on heuristinen — mehiläiset lentävät joskus epäoptimaalisissa oloissa.
SOURCE_REGISTRY:
  sources:
  - id: src:LSA1
    org: SML
    title: Mehiläishoitoa käytännössä
    year: 2011
    url: null
    identifier: ISBN 978-952-92-9184-4
    supports: Lentolämpötilat, sää-optimaalisuus.
  - id: src:LSA2
    org: LuontoPortti / Luke
    title: Kasvien nektarieritys ja sääolosuhteet
    year: 2024
    url: https://luontoportti.com/
    supports: Nektarierityksen sääriippuvuus.
eval_questions:
- q: Mikä on mehiläisten minimilentolämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.value
  source: src:LSA1
- q: Mikä on optimaalinen lentolämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.note
  source: src:LSA1
- q: Mikä tuulennopeus estää lennon?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.max_wind_speed_flight_ms.value
  source: src:LSA1
- q: Milloin sade estää lennon?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.rain_flight_stop.value
  source: src:LSA1
- q: Mitkä ovat optimaaliset lento-olot?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value
  source: src:LSA1
- q: Miten ilmankosteus vaikuttaa nektarieritykseen?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.value
  source: src:LSA2
- q: Milloin on sopivaa tarkistaa pesä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.inspection_weather.value
  source: src:LSA1
- q: Mitä tapahtuu pitkässä sadejaksossa?
  a_ref: FAILURE_MODES[0].action
  source: src:LSA1
- q: Mitä keväinen pakkasyö aiheuttaa?
  a_ref: FAILURE_MODES[1].action
  source: src:LSA1
- q: Mikä on puhdistuslento?
  a_ref: SEASONAL_RULES[0].action
  source: src:LSA1
- q: Mitä helteellä tehdään tarhalla?
  a_ref: SEASONAL_RULES[1].action
  source: src:LSA1
- q: Milloin oksaalihappohoito tehdään?
  a_ref: SEASONAL_RULES[2].action
  source: src:LSA1
- q: Onko talvella lentotoimintaa?
  a_ref: SEASONAL_RULES[3].action
  source: src:LSA1
- q: Kenelle huonoista oloista ilmoitetaan?
  a_ref: PROCESS_FLOWS.daily_assessment.steps
  source: src:LSA1
- q: Miten pitkä sadejakso havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:LSA1
- q: Kenelle nektari-ongelmasta ilmoitetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.action
  source: src:LSA2
- q: Mikä pilvisyys on lennon raja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value
  source: src:LSA1
- q: Voiko lento-oloindeksiin luottaa täysin?
  a_ref: UNCERTAINTY_NOTES
  source: src:LSA1
- q: Mikä on poikkeuksellisen talvipäivän merkki?
  a_ref: SEASONAL_RULES[3].action
  source: src:LSA1
- q: Miten helle vaikuttaa mehiläisiin?
  a_ref: SEASONAL_RULES[1].action
  source: src:LSA1
- q: Mikä on normaali keräilylämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.note
  source: src:LSA1
- q: Mitä parveiluvahdille kerrotaan hyvästä säästä?
  a_ref: PROCESS_FLOWS.daily_assessment.steps
  source: src:LSA1
- q: Mikä on kosteuteen liittyvä hälytysraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.action
  source: src:LSA2
- q: Paljonko sade vähentää lentoja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.rain_flight_stop.value
  source: src:LSA1
- q: Miten mikroilmasto vaikuttaa ennusteeseen?
  a_ref: UNCERTAINTY_NOTES
  source: src:LSA1
- q: Mikä on tuulen raja optimaalisille oloille?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value
  source: src:LSA1
- q: Miten lento-oloindeksi lasketaan?
  a_ref: PROCESS_FLOWS.daily_assessment.steps
  source: src:LSA1
- q: Miksi pesää ei saa avata keväisessä pakkasessa?
  a_ref: FAILURE_MODES[1].action
  source: src:LSA1
- q: Milloin viimeiset +10°C päivät ovat?
  a_ref: SEASONAL_RULES[2].action
  source: src:LSA1
- q: Miten ruokintatarve liittyy säätilanteeseen?
  a_ref: FAILURE_MODES[0].action
  source: src:LSA1
- q: Mikä on mehiläisten reaktio >30°C lämpöön?
  a_ref: SEASONAL_RULES[1].action
  source: src:LSA1
- q: Milloin ensimmäiset lentopäivät ovat?
  a_ref: SEASONAL_RULES[0].action
  source: src:LSA1
- q: Mikä on +5°C talvipäivän merkitys?
  a_ref: SEASONAL_RULES[3].action
  source: src:LSA1
- q: Mistä sääennuste haetaan?
  a_ref: PROCESS_FLOWS.daily_assessment.steps
  source: src:LSA1
- q: Mikä on sadejakson kesto ennen hälytystausta?
  a_ref: FAILURE_MODES[0].detection
  source: src:LSA1
- q: Voivatko mehiläiset lentää epäoptimaalisissa oloissa?
  a_ref: UNCERTAINTY_NOTES
  source: src:LSA1
- q: Miten 24h ennustetta käytetään?
  a_ref: PROCESS_FLOWS.daily_assessment.steps
  source: src:LSA1
- q: Mikä on sikiön riski keväisessä pakkasessa?
  a_ref: FAILURE_MODES[1].action
  source: src:LSA1
- q: Milloin ruokavaroja tarkkaillaan erityisesti?
  a_ref: FAILURE_MODES[0].action
  source: src:LSA1
- q: Mikä on keräilyn aloituslämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.note
  source: src:LSA1
```

**sources.yaml:**
```yaml
sources:
- id: src:LSA1
  org: SML
  title: Mehiläishoitoa käytännössä
  year: 2011
  url: null
  identifier: ISBN 978-952-92-9184-4
  supports: Lentolämpötilat, sää-optimaalisuus.
- id: src:LSA2
  org: LuontoPortti / Luke
  title: Kasvien nektarieritys ja sääolosuhteet
  year: 2024
  url: https://luontoportti.com/
  supports: Nektarierityksen sääriippuvuus.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Lentosää-analyytikko
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Mehiläisten lentoaktiivisuuden sää-arviointi
- Yhdistää meteorologin datan mehiläishoidon päätöksiin
- Korvenranta + muut tarha-alueet

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| min_flight_temp_c | 10 | T<10°C → EI lentoa, ei tarkastuskäyntiä. 10-13°C → vähäinen aktiivisuus. >15°C optimaalinen. Ilmoita tarhaajalle tarkistusikkunat. | src:LEN1 |
| max_wind_speed_flight_ms | 8 | >8 m/s → mehiläisten aktiivisuus -50%. >12 m/s → ei lentoa. Tuuleton + aurinko + T>15°C = täysaktiivisuus. | src:LEN1 |
| rain_flight_stop | Sade >0.5 mm/h → mehiläiset eivät lennä | >0.5 mm/h → ei lentoa. Sade >3 pv kesä-heinäkuussa → tarkista ruokavarasto (kulutus ilman tuontia ~0.5 kg/pv). | src:LEN1 |
| optimal_flying_conditions | T >15°C, tuuli <5 m/s, ei sadetta, pilvisyys <6/8 | — | src:LSA1 |
| nectar_secretion_humidity | Ilmankosteus 50-80% → meden eritys optimaalista | Alle 40% → kasvit eivät eritä, ilmoita nektari-informaatikolle | src:LSA2 |
| inspection_weather | T >15°C, ei sadetta, tuuli <5 m/s → sopiva pesäntarkistukselle | — | src:LSA1 |

## Tietotaulukot

**Lentosää-analyytikko — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| min_flight_temp_c | 10 | T<10°C → EI lentoa, ei tarkastuskäyntiä. 10-13°C → vähäinen aktiivisuus. >15°C o |
| max_wind_speed_flight_ms | 8 | >8 m/s → mehiläisten aktiivisuus -50%. >12 m/s → ei lentoa. Tuuleton + aurinko + |
| rain_flight_stop | Sade >0.5 mm/h → mehiläiset eivät lennä | >0.5 mm/h → ei lentoa. Sade >3 pv kesä-heinäkuussa → tarkista ruokavarasto (kulu |
| optimal_flying_conditions | T >15°C, tuuli <5 m/s, ei sadetta, pilvisyys <6/8 |  |
| nectar_secretion_humidity | Ilmankosteus 50-80% → meden eritys optimaalista | Alle 40% → kasvit eivät eritä, ilmoita nektari-informaatikolle |
| inspection_weather | T >15°C, ei sadetta, tuuli <5 m/s → sopiva pesäntarkistukselle |  |

## Prosessit

**daily_assessment:**
  1. Hae sääennuste meteorologi-agentilta (3h ja 24h)
  2. Laske lento-oloindeksi (T, tuuli, sade, pilvisyys)
  3. Jos indeksi 'hyvä' → ilmoita tarhaajalle optimaalisista toimintaajoista
  4. Jos indeksi 'huono' >3pv → varoita tarhaajaa (pesät eivät keräile, ruokavarat laskevat)
  5. Ilmoita parveiluvahdille: hyvät olosuhteet = korkea parveilriski

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | Ensimmäiset lentopäivät (>10°C) kriittisiä pesän kunnon indikaattorina. Puhdistuslento. | src:LSA1 |
| **Kesä** | Optimaalinen keräilykausi. Helle >30°C → mehiläiset tuulettavat, lisää vettä tarhan lähelle. | src:LSA1 |
| **Syksy** | Lentopäivät vähenevät. Viimeiset +10°C päivät → oksaalihappohoito (pesimätön kausi alkaa). | src:LSA1 |
| **Talvi** | Ei lentotoimintaa. Poikkeukselliset +5°C päivät → mehiläisten ulostuskierto (positiivinen merkki). | src:LSA1 |

## Virhe- ja vaaratilanteet

### ⚠️ Pitkä sadejakso keräilykaudella
- **Havaitseminen:** Sade >3 vrk peräkkäin kesä-heinäkuussa
- **Toimenpide:** Tarkkaile pesien ruokavarastoja, harkitse hätäruokintaa
- **Lähde:** src:LSA1

### ⚠️ Yllättävä pakkasvuorokausi keväällä
- **Havaitseminen:** T <0°C huhtikuussa lentokauden jälkeen
- **Toimenpide:** Ilmoita tarhaajalle: pesää ei saa avata, riskinä sikiön kylmettyminen
- **Lähde:** src:LSA1

## Epävarmuudet
- Mikroilmasto tarhan ympäristössä voi poiketa yleisennusteesta ±2°C.
- Lento-oloindeksi on heuristinen — mehiläiset lentävät joskus epäoptimaalisissa oloissa.

## Lähteet
- **src:LSA1**: SML — *Mehiläishoitoa käytännössä* (2011) —
- **src:LSA2**: LuontoPortti / Luke — *Kasvien nektarieritys ja sääolosuhteet* (2024) https://luontoportti.com/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on mehiläisten minimilentolämpötila?**
    → `DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.value` [src:LSA1]
 2. **Mikä on optimaalinen lentolämpötila?**
    → `DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.note` [src:LSA1]
 3. **Mikä tuulennopeus estää lennon?**
    → `DECISION_METRICS_AND_THRESHOLDS.max_wind_speed_flight_ms.value` [src:LSA1]
 4. **Milloin sade estää lennon?**
    → `DECISION_METRICS_AND_THRESHOLDS.rain_flight_stop.value` [src:LSA1]
 5. **Mitkä ovat optimaaliset lento-olot?**
    → `DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value` [src:LSA1]
 6. **Miten ilmankosteus vaikuttaa nektarieritykseen?**
    → `DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.value` [src:LSA2]
 7. **Milloin on sopivaa tarkistaa pesä?**
    → `DECISION_METRICS_AND_THRESHOLDS.inspection_weather.value` [src:LSA1]
 8. **Mitä tapahtuu pitkässä sadejaksossa?**
    → `FAILURE_MODES[0].action` [src:LSA1]
 9. **Mitä keväinen pakkasyö aiheuttaa?**
    → `FAILURE_MODES[1].action` [src:LSA1]
10. **Mikä on puhdistuslento?**
    → `SEASONAL_RULES[0].action` [src:LSA1]
11. **Mitä helteellä tehdään tarhalla?**
    → `SEASONAL_RULES[1].action` [src:LSA1]
12. **Milloin oksaalihappohoito tehdään?**
    → `SEASONAL_RULES[2].action` [src:LSA1]
13. **Onko talvella lentotoimintaa?**
    → `SEASONAL_RULES[3].action` [src:LSA1]
14. **Kenelle huonoista oloista ilmoitetaan?**
    → `PROCESS_FLOWS.daily_assessment.steps` [src:LSA1]
15. **Miten pitkä sadejakso havaitaan?**
    → `FAILURE_MODES[0].detection` [src:LSA1]
16. **Kenelle nektari-ongelmasta ilmoitetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.action` [src:LSA2]
17. **Mikä pilvisyys on lennon raja?**
    → `DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value` [src:LSA1]
18. **Voiko lento-oloindeksiin luottaa täysin?**
    → `UNCERTAINTY_NOTES` [src:LSA1]
19. **Mikä on poikkeuksellisen talvipäivän merkki?**
    → `SEASONAL_RULES[3].action` [src:LSA1]
20. **Miten helle vaikuttaa mehiläisiin?**
    → `SEASONAL_RULES[1].action` [src:LSA1]
21. **Mikä on normaali keräilylämpötila?**
    → `DECISION_METRICS_AND_THRESHOLDS.min_flight_temp_c.note` [src:LSA1]
22. **Mitä parveiluvahdille kerrotaan hyvästä säästä?**
    → `PROCESS_FLOWS.daily_assessment.steps` [src:LSA1]
23. **Mikä on kosteuteen liittyvä hälytysraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_humidity.action` [src:LSA2]
24. **Paljonko sade vähentää lentoja?**
    → `DECISION_METRICS_AND_THRESHOLDS.rain_flight_stop.value` [src:LSA1]
25. **Miten mikroilmasto vaikuttaa ennusteeseen?**
    → `UNCERTAINTY_NOTES` [src:LSA1]
26. **Mikä on tuulen raja optimaalisille oloille?**
    → `DECISION_METRICS_AND_THRESHOLDS.optimal_flying_conditions.value` [src:LSA1]
27. **Miten lento-oloindeksi lasketaan?**
    → `PROCESS_FLOWS.daily_assessment.steps` [src:LSA1]
28. **Miksi pesää ei saa avata keväisessä pakkasessa?**
    → `FAILURE_MODES[1].action` [src:LSA1]
29. **Milloin viimeiset +10°C päivät ovat?**
    → `SEASONAL_RULES[2].action` [src:LSA1]
30. **Miten ruokintatarve liittyy säätilanteeseen?**
    → `FAILURE_MODES[0].action` [src:LSA1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 14
## AGENT 14: Parveiluvahti
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: parveiluvahti
  agent_name: Parveiluvahti
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Valvoo parveiluriskiä kaikilla tarhoilla
- Saa dataa pesälämpö-agentilta, lentosää-agentilta ja tarhaajalta
DECISION_METRICS_AND_THRESHOLDS:
  swarm_season:
    value: Touko-heinäkuu, huippu kesäkuun 2. viikko
    source: src:PAR1
  inspection_interval_days:
    value: 7
    action: Parveilukauden aikana tarkistus 7 pv välein
    source: src:PAR1
  queen_cell_count_trigger:
    value: 1
    action: ≥1 emokoppa → välitön toimenpide (jako tai poisto)
    source: src:PAR1
  colony_overcrowding_indicator:
    value: Kaarella (pesän ulkopuolella) >200 mehiläistä illalla
    action: Lisää korotus tai jaa pesä
    source: src:PAR1
  weather_swarm_risk:
    value: T >20°C, tuuleton, aurinkoinen → korkea parveilupäivä
    source: src:PAR1
  post_swarm_signs:
    value: Äkillinen mehiläismäärän lasku, tyhjät emokopat
    action: Tarkista onko emo jäljellä, sulje ylimääräiset lennot
    source: src:PAR1
SEASONAL_RULES:
- season: Kevät (touko)
  action: Parveilukausi alkaa. Aloita 7pv tarkistussykli. Varmista tilaa pesässä.
  source: src:PAR1
- season: Kesä (kesä-heinä)
  action: '[vko 22-35] Huippukausi. Hellepäivinä erityisvarovaisuus. Parviloukut paikoilleen.'
  source: src:PAR1
- season: Syksy
  action: '[vko 36-48] Parveiluriski ohi. Tarkista emontilanne: onko uusi emo muniva?'
  source: src:PAR1
- season: Talvi
  action: '[vko 49-13] Ei parveiluriskiä. Suunnittele kevään ehkäisytoimet.'
  source: src:PAR1
FAILURE_MODES:
- mode: Parvi lähtenyt
  detection: Suuri mehiläispilvi ilmassa, pesän populaatio pudonnut äkisti
  action: Paikanna parvi (usein lähipuussa 24h), kerää kiinni, aseta uuteen pesään
  source: src:PAR1
- mode: Emoton pesä parveilun jälkeen
  detection: Ei emoa eikä avoimia emokoppia 2 viikon jälkeen
  action: Yhdistä lehtipaperilla tai anna uusi emo
  source: src:PAR1
PROCESS_FLOWS:
  swarm_prevention:
    steps:
    - 1. Tarkista emokopat joka 7. pv touko-heinäkuussa
    - '2. Jos emokoppia → päätä: jako vai poisto'
    - '3. Jako: siirrä vanha emo + 3 kehystä uuteen pesään'
    - '4. Poisto: murskaa kaikki emokopat (EI jätä yhtäkään)'
    - 5. Lisää tilaa (korotus) jos pesä ahdas
KNOWLEDGE_TABLES:
  swarm_triggers:
  - trigger: Emokopat rakennettu
    severity: KRIITTINEN
    response_time: 24h
  - trigger: Kaarella >200 mehiläistä
    severity: KORKEA
    response_time: 48h
  - trigger: Pesässä >10 kehystä sikiötä
    severity: KESKITASO
    response_time: 7 pv
  - trigger: Uusi emo kuoriutumassa
    severity: KRIITTINEN
    response_time: Välitön
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Parveilu on mehiläisten luontainen lisääntymistapa — täydellinen esto ei aina mahdollista.
SOURCE_REGISTRY:
  sources:
  - id: src:PAR1
    org: SML
    title: Mehiläishoitoa käytännössä
    year: 2011
    url: null
    identifier: ISBN 978-952-92-9184-4
    supports: Parveilunhallinta.
eval_questions:
- q: Mikä on parveilukausi?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.swarm_season.value
  source: src:PAR1
- q: Kuinka usein emokopat tarkistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.inspection_interval_days.value
  source: src:PAR1
- q: Montako emokoppaa laukaisee toimenpiteen?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.queen_cell_count_trigger.value
  source: src:PAR1
- q: Mikä on kaarella-ilmiön raja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.value
  source: src:PAR1
- q: Millainen sää lisää parveiluriskiä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.weather_swarm_risk.value
  source: src:PAR1
- q: Miten parvi kerätään?
  a_ref: FAILURE_MODES[0].action
  source: src:PAR1
- q: Miten jako tehdään?
  a_ref: PROCESS_FLOWS.swarm_prevention.steps
  source: src:PAR1
- q: Mikä on emokopin vastausaika?
  a_ref: KNOWLEDGE_TABLES.swarm_triggers[0].response_time
  source: src:PAR1
- q: Mitä tehdään parveilun jälkeisessä emottomassa pesässä?
  a_ref: FAILURE_MODES[1].action
  source: src:PAR1
- q: Milloin huippukausi on?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.swarm_season.value
  source: src:PAR1
- q: Miten emokopat poistetaan?
  a_ref: PROCESS_FLOWS.swarm_prevention.steps
  source: src:PAR1
- q: Mitä ovat parveilun jälkeiset merkit?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.post_swarm_signs.value
  source: src:PAR1
- q: Miksi kaikkia emokoppia ei saa jättää?
  a_ref: PROCESS_FLOWS.swarm_prevention.steps
  source: src:PAR1
- q: Mistä tietää onko parvi lähtenyt?
  a_ref: FAILURE_MODES[0].detection
  source: src:PAR1
- q: Milloin parveilukausi päättyy?
  a_ref: SEASONAL_RULES[2].action
  source: src:PAR1
- q: Mikä on parviloukkujen käyttöaika?
  a_ref: SEASONAL_RULES[1].action
  source: src:PAR1
- q: Voiko parveilun kokonaan estää?
  a_ref: UNCERTAINTY_NOTES
  source: src:PAR1
- q: Miten tila vaikuttaa parveiluun?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.action
  source: src:PAR1
- q: Mitä talvella suunnitellaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:PAR1
- q: Kuinka nopeasti emokopista kuoriutuu?
  a_ref: KNOWLEDGE_TABLES.swarm_triggers[3].response_time
  source: src:PAR1
- q: Miten pesän populaation lasku havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:PAR1
- q: Onko 10 sikiökehystä riskitekijä?
  a_ref: KNOWLEDGE_TABLES.swarm_triggers[2]
  source: src:PAR1
- q: Miten ahdas pesä tunnistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.value
  source: src:PAR1
- q: Mikä on korotuksen tarkoitus?
  a_ref: PROCESS_FLOWS.swarm_prevention.steps
  source: src:PAR1
- q: Mistä parvi löytyy lähtemisen jälkeen?
  a_ref: FAILURE_MODES[0].action
  source: src:PAR1
- q: Miten jako tehdään konkreettisesti?
  a_ref: PROCESS_FLOWS.swarm_prevention.steps
  source: src:PAR1
- q: Pitääkö uutta emoa odottaa 2 viikkoa?
  a_ref: FAILURE_MODES[1].detection
  source: src:PAR1
- q: Millainen on korkean riskin tarkistussykli?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.inspection_interval_days
  source: src:PAR1
- q: Miten hellepäivät liittyvät parveiluun?
  a_ref: SEASONAL_RULES[1].action
  source: src:PAR1
- q: Miten emoton pesä yhdistetään?
  a_ref: FAILURE_MODES[1].action
  source: src:PAR1
- q: 'Operatiivinen päätöskysymys #1?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #2?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #3?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #4?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #5?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #6?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #7?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #8?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #9?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
- q: 'Operatiivinen päätöskysymys #10?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PARV
```

**sources.yaml:**
```yaml
sources:
- id: src:PAR1
  org: SML
  title: Mehiläishoitoa käytännössä
  year: 2011
  url: null
  identifier: ISBN 978-952-92-9184-4
  supports: Parveilunhallinta.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Parveiluvahti
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Valvoo parveiluriskiä kaikilla tarhoilla
- Saa dataa pesälämpö-agentilta, lentosää-agentilta ja tarhaajalta

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| swarm_season | Touko-heinäkuu, huippu kesäkuun 2. viikko | — | src:PAR1 |
| inspection_interval_days | 7 | Parveilukauden aikana tarkistus 7 pv välein | src:PAR1 |
| queen_cell_count_trigger | 1 | ≥1 emokoppa → välitön toimenpide (jako tai poisto) | src:PAR1 |
| colony_overcrowding_indicator | Kaarella (pesän ulkopuolella) >200 mehiläistä illalla | Lisää korotus tai jaa pesä | src:PAR1 |
| weather_swarm_risk | T >20°C, tuuleton, aurinkoinen → korkea parveilupäivä | — | src:PAR1 |
| post_swarm_signs | Äkillinen mehiläismäärän lasku, tyhjät emokopat | Tarkista onko emo jäljellä, sulje ylimääräiset lennot | src:PAR1 |

## Tietotaulukot

**swarm_triggers:**

| trigger | severity | response_time |
| --- | --- | --- |
| Emokopat rakennettu | KRIITTINEN | 24h |
| Kaarella >200 mehiläistä | KORKEA | 48h |
| Pesässä >10 kehystä sikiötä | KESKITASO | 7 pv |
| Uusi emo kuoriutumassa | KRIITTINEN | Välitön |

## Prosessit

**swarm_prevention:**
  1. Tarkista emokopat joka 7. pv touko-heinäkuussa
  2. Jos emokoppia → päätä: jako vai poisto
  3. Jako: siirrä vanha emo + 3 kehystä uuteen pesään
  4. Poisto: murskaa kaikki emokopat (EI jätä yhtäkään)
  5. Lisää tilaa (korotus) jos pesä ahdas

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät (touko)** | Parveilukausi alkaa. Aloita 7pv tarkistussykli. Varmista tilaa pesässä. | src:PAR1 |
| **Kesä (kesä-heinä)** | [vko 22-35] Huippukausi. Hellepäivinä erityisvarovaisuus. Parviloukut paikoilleen. | src:PAR1 |
| **Syksy** | [vko 36-48] Parveiluriski ohi. Tarkista emontilanne: onko uusi emo muniva? | src:PAR1 |
| **Talvi** | [vko 49-13] Ei parveiluriskiä. Suunnittele kevään ehkäisytoimet. | src:PAR1 |

## Virhe- ja vaaratilanteet

### ⚠️ Parvi lähtenyt
- **Havaitseminen:** Suuri mehiläispilvi ilmassa, pesän populaatio pudonnut äkisti
- **Toimenpide:** Paikanna parvi (usein lähipuussa 24h), kerää kiinni, aseta uuteen pesään
- **Lähde:** src:PAR1

### ⚠️ Emoton pesä parveilun jälkeen
- **Havaitseminen:** Ei emoa eikä avoimia emokoppia 2 viikon jälkeen
- **Toimenpide:** Yhdistä lehtipaperilla tai anna uusi emo
- **Lähde:** src:PAR1

## Epävarmuudet
- Parveilu on mehiläisten luontainen lisääntymistapa — täydellinen esto ei aina mahdollista.

## Lähteet
- **src:PAR1**: SML — *Mehiläishoitoa käytännössä* (2011) —

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on parveilukausi?**
    → `DECISION_METRICS_AND_THRESHOLDS.swarm_season.value` [src:PAR1]
 2. **Kuinka usein emokopat tarkistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.inspection_interval_days.value` [src:PAR1]
 3. **Montako emokoppaa laukaisee toimenpiteen?**
    → `DECISION_METRICS_AND_THRESHOLDS.queen_cell_count_trigger.value` [src:PAR1]
 4. **Mikä on kaarella-ilmiön raja?**
    → `DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.value` [src:PAR1]
 5. **Millainen sää lisää parveiluriskiä?**
    → `DECISION_METRICS_AND_THRESHOLDS.weather_swarm_risk.value` [src:PAR1]
 6. **Miten parvi kerätään?**
    → `FAILURE_MODES[0].action` [src:PAR1]
 7. **Miten jako tehdään?**
    → `PROCESS_FLOWS.swarm_prevention.steps` [src:PAR1]
 8. **Mikä on emokopin vastausaika?**
    → `KNOWLEDGE_TABLES.swarm_triggers[0].response_time` [src:PAR1]
 9. **Mitä tehdään parveilun jälkeisessä emottomassa pesässä?**
    → `FAILURE_MODES[1].action` [src:PAR1]
10. **Milloin huippukausi on?**
    → `DECISION_METRICS_AND_THRESHOLDS.swarm_season.value` [src:PAR1]
11. **Miten emokopat poistetaan?**
    → `PROCESS_FLOWS.swarm_prevention.steps` [src:PAR1]
12. **Mitä ovat parveilun jälkeiset merkit?**
    → `DECISION_METRICS_AND_THRESHOLDS.post_swarm_signs.value` [src:PAR1]
13. **Miksi kaikkia emokoppia ei saa jättää?**
    → `PROCESS_FLOWS.swarm_prevention.steps` [src:PAR1]
14. **Mistä tietää onko parvi lähtenyt?**
    → `FAILURE_MODES[0].detection` [src:PAR1]
15. **Milloin parveilukausi päättyy?**
    → `SEASONAL_RULES[2].action` [src:PAR1]
16. **Mikä on parviloukkujen käyttöaika?**
    → `SEASONAL_RULES[1].action` [src:PAR1]
17. **Voiko parveilun kokonaan estää?**
    → `UNCERTAINTY_NOTES` [src:PAR1]
18. **Miten tila vaikuttaa parveiluun?**
    → `DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.action` [src:PAR1]
19. **Mitä talvella suunnitellaan?**
    → `SEASONAL_RULES[3].action` [src:PAR1]
20. **Kuinka nopeasti emokopista kuoriutuu?**
    → `KNOWLEDGE_TABLES.swarm_triggers[3].response_time` [src:PAR1]
21. **Miten pesän populaation lasku havaitaan?**
    → `FAILURE_MODES[0].detection` [src:PAR1]
22. **Onko 10 sikiökehystä riskitekijä?**
    → `KNOWLEDGE_TABLES.swarm_triggers[2]` [src:PAR1]
23. **Miten ahdas pesä tunnistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.value` [src:PAR1]
24. **Mikä on korotuksen tarkoitus?**
    → `PROCESS_FLOWS.swarm_prevention.steps` [src:PAR1]
25. **Mistä parvi löytyy lähtemisen jälkeen?**
    → `FAILURE_MODES[0].action` [src:PAR1]
26. **Miten jako tehdään konkreettisesti?**
    → `PROCESS_FLOWS.swarm_prevention.steps` [src:PAR1]
27. **Pitääkö uutta emoa odottaa 2 viikkoa?**
    → `FAILURE_MODES[1].detection` [src:PAR1]
28. **Millainen on korkean riskin tarkistussykli?**
    → `DECISION_METRICS_AND_THRESHOLDS.inspection_interval_days` [src:PAR1]
29. **Miten hellepäivät liittyvät parveiluun?**
    → `SEASONAL_RULES[1].action` [src:PAR1]
30. **Miten emoton pesä yhdistetään?**
    → `FAILURE_MODES[1].action` [src:PAR1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 15
## AGENT 15: Pesälämpö- ja kosteusmittaaja
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: pesalampo
  agent_name: Pesälämpö- ja kosteusmittaaja
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- IoT-anturit pesässä (lämpö, kosteus, paino)
- BLE/WiFi → paikallinen gateway → tietokanta
DECISION_METRICS_AND_THRESHOLDS:
  brood_nest_temp_c:
    value: 34-36
    action: <34°C → sikiö kehittyy hitaasti, >37°C → sikiövauriot
    source: src:PES1
  winter_cluster_core_c:
    value: 20-35
    action: <20°C → kriittinen, yhdyskunta heikkenee
    source: src:PES1
  hive_humidity_rh_pct:
    value: 50-70
    action: '>80% → homevaara, tuuletusaukot, <40% → kuivuusstressi'
    source: src:PES1
  weight_loss_winter_kg_per_week:
    value: 0.3
    action: '>0.5 kg/vko → ruokavarasto hupenee, harkitse ruokintaa'
    source: src:PES1
  sudden_weight_drop_kg:
    value: 2
    action: '>2 kg äkillinen pudotus → parveilu tai ryöstö, ilmoita tarhaajalle'
    source: src:PES1
  spring_weight_increase_start:
    value: Painonnousu keväällä → meden keräily alkaa, ilmoita tarhaajalle
    source: src:PES1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Sikiöpesän lämpötilan seuranta erityisen kriittistä. Painonnousu
    osoittaa keräilyn alkua.'
  source: src:PES1
- season: Kesä
  action: '[vko 22-35] Ylilämpenemisriski helteellä. Kosteus nousee linkoamisen aikoihin.
    Painon nopea nousu = satohuippu.'
  source: src:PES1
- season: Syksy
  action: '[vko 36-48] Painon stabiloituminen syysruokinnan jälkeen. Kosteus seurannassa
    (homevaara märässä syksyssä).'
  source: src:PES1
- season: Talvi
  action: Jatkuva painonseuranta (0.3 kg/vko normaali). Ydinlämpö >20°C. Kosteus 50-70%.
  source: src:PES1
FAILURE_MODES:
- mode: Anturi ei lähetä dataa
  detection: Ei dataa >1h
  action: Tarkista akku, BLE-yhteys, ilmoita laitehuoltajalle
  source: src:PES1
- mode: Lämpötila putoaa äkisti
  detection: '>5°C pudotus 2h sisällä'
  action: 'HÄLYTYS tarhaajalle: mahdollinen emokato tai parveilu'
  source: src:PES1
PROCESS_FLOWS:
  monitoring:
    steps:
    - 1. Lue anturi 15 min välein
    - 2. Vertaa kynnysarvoihin
    - 3. Jos poikkeama → HÄLYTYS relevanteille agenteille
    - 4. Tallenna aikasarja tietokantaan
    - 5. Viikkoraportti tarhaajalle
KNOWLEDGE_TABLES:
- table_id: TBL_PESA_01
  title: Pesälämpö- ja kosteusmittaaja — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: brood_nest_temp_c
    value: 34-36
    action: <34°C → sikiö kehittyy hitaasti, >37°C → sikiövauriot
  - metric: winter_cluster_core_c
    value: 20-35
    action: <20°C → kriittinen, yhdyskunta heikkenee
  - metric: hive_humidity_rh_pct
    value: 50-70
    action: '>80% → homevaara, tuuletusaukot, <40% → kuivuusstressi'
  - metric: weight_loss_winter_kg_per_week
    value: '0.3'
    action: '>0.5 kg/vko → ruokavarasto hupenee, harkitse ruokintaa'
  - metric: sudden_weight_drop_kg
    value: '2'
    action: '>2 kg äkillinen pudotus → parveilu tai ryöstö, ilmoita tarhaajalle'
  - metric: spring_weight_increase_start
    value: Painonnousu keväällä → meden keräily alkaa, ilmoita tarhaajalle
    action: ''
  source: src:PESA
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Anturin sijainti pesässä vaikuttaa lukemin — reuna vs. keskusta voi erota 10°C.
SOURCE_REGISTRY:
  sources:
  - id: src:PES1
    org: SML / Arnia Ltd
    title: Pesänseurantatekniikka
    year: 2024
    url: https://www.arnia.co.uk/
    supports: IoT-pesäseuranta, lämpö-/kosteus-/painodata.
eval_questions:
- q: Mikä on sikiöpesän normaalilämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.brood_nest_temp_c.value
  source: src:PES1
- q: Mikä on talvipallon kriittinen alaraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_c.action
  source: src:PES1
- q: Mikä on kosteuteen normaali vaihteluväli?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.value
  source: src:PES1
- q: Mikä on normaali talvinen painohäviö per viikko?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.value
  source: src:PES1
- q: Mikä äkillinen painonpudotus tarkoittaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action
  source: src:PES1
- q: Kuinka usein anturia luetaan?
  a_ref: PROCESS_FLOWS.monitoring.steps
  source: src:PES1
- q: Mitä >80% kosteus aiheuttaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action
  source: src:PES1
- q: Miten painon nousu keväällä tulkitaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.spring_weight_increase_start.value
  source: src:PES1
- q: Mitä tapahtuu anturikatossa?
  a_ref: FAILURE_MODES[0].action
  source: src:PES1
- q: Mikä laukaisee lämpötilahälytyksen?
  a_ref: FAILURE_MODES[1].detection
  source: src:PES1
- q: Mikä on sikiövaurion lämpötilaraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.brood_nest_temp_c.action
  source: src:PES1
- q: Kenelle painohälytyksestä ilmoitetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action
  source: src:PES1
- q: Mikä on ylilämpenemisriski kesällä?
  a_ref: SEASONAL_RULES[1].action
  source: src:PES1
- q: Miten syysruokinnan onnistuminen näkyy painossa?
  a_ref: SEASONAL_RULES[2].action
  source: src:PES1
- q: Mikä on normaali talvipallon lämpötilaväli?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_c.value
  source: src:PES1
- q: Miten painonpudotusraja lasketaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.action
  source: src:PES1
- q: Mitä äkillinen lämpötilan lasku voi tarkoittaa?
  a_ref: FAILURE_MODES[1].action
  source: src:PES1
- q: Mikä on satohuipun painosignaali?
  a_ref: SEASONAL_RULES[1].action
  source: src:PES1
- q: Miten anturin paikka vaikuttaa lukemiin?
  a_ref: UNCERTAINTY_NOTES
  source: src:PES1
- q: Mikä on kuivuusstressin kosteusraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action
  source: src:PES1
- q: Mikä on viikkoraportin sisältö?
  a_ref: PROCESS_FLOWS.monitoring.steps
  source: src:PES1
- q: Miten homevaara tunnistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action
  source: src:PES1
- q: Mikä on BLE-yhteyden tarkistustapa?
  a_ref: FAILURE_MODES[0].action
  source: src:PES1
- q: Kuinka usein data tallennetaan?
  a_ref: PROCESS_FLOWS.monitoring.steps
  source: src:PES1
- q: Miten keväällä lämpötilaa seurataan?
  a_ref: SEASONAL_RULES[0].action
  source: src:PES1
- q: Mikä on painon nousun merkitys keväällä?
  a_ref: SEASONAL_RULES[0].action
  source: src:PES1
- q: Mikä on kriittisen talvinen viikkohäviö?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.action
  source: src:PES1
- q: Miten ryöstö näkyy painossa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action
  source: src:PES1
- q: Kenelle anturivika ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:PES1
- q: Mikä on datan hälytysviive?
  a_ref: FAILURE_MODES[0].detection
  source: src:PES1
- q: 'Operatiivinen päätöskysymys #1?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #2?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #3?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #4?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #5?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #6?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #7?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #8?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #9?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #10?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
```

**sources.yaml:**
```yaml
sources:
- id: src:PES1
  org: SML / Arnia Ltd
  title: Pesänseurantatekniikka
  year: 2024
  url: https://www.arnia.co.uk/
  supports: IoT-pesäseuranta, lämpö-/kosteus-/painodata.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Pesälämpö- ja kosteusmittaaja
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- IoT-anturit pesässä (lämpö, kosteus, paino)
- BLE/WiFi → paikallinen gateway → tietokanta

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| brood_nest_temp_c | 34-36 | <34°C → sikiö kehittyy hitaasti, >37°C → sikiövauriot | src:PES1 |
| winter_cluster_core_c | 20-35 | <20°C → kriittinen, yhdyskunta heikkenee | src:PES1 |
| hive_humidity_rh_pct | 50-70 | >80% → homevaara, tuuletusaukot, <40% → kuivuusstressi | src:PES1 |
| weight_loss_winter_kg_per_week | 0.3 | >0.5 kg/vko → ruokavarasto hupenee, harkitse ruokintaa | src:PES1 |
| sudden_weight_drop_kg | 2 | >2 kg äkillinen pudotus → parveilu tai ryöstö, ilmoita tarhaajalle | src:PES1 |
| spring_weight_increase_start | Painonnousu keväällä → meden keräily alkaa, ilmoita tarhaajalle | — | src:PES1 |

## Tietotaulukot

**Pesälämpö- ja kosteusmittaaja — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| brood_nest_temp_c | 34-36 | <34°C → sikiö kehittyy hitaasti, >37°C → sikiövauriot |
| winter_cluster_core_c | 20-35 | <20°C → kriittinen, yhdyskunta heikkenee |
| hive_humidity_rh_pct | 50-70 | >80% → homevaara, tuuletusaukot, <40% → kuivuusstressi |
| weight_loss_winter_kg_per_week | 0.3 | >0.5 kg/vko → ruokavarasto hupenee, harkitse ruokintaa |
| sudden_weight_drop_kg | 2 | >2 kg äkillinen pudotus → parveilu tai ryöstö, ilmoita tarhaajalle |
| spring_weight_increase_start | Painonnousu keväällä → meden keräily alkaa, ilmoita tarhaajalle |  |

## Prosessit

**monitoring:**
  1. Lue anturi 15 min välein
  2. Vertaa kynnysarvoihin
  3. Jos poikkeama → HÄLYTYS relevanteille agenteille
  4. Tallenna aikasarja tietokantaan
  5. Viikkoraportti tarhaajalle

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Sikiöpesän lämpötilan seuranta erityisen kriittistä. Painonnousu osoittaa keräilyn alkua. | src:PES1 |
| **Kesä** | [vko 22-35] Ylilämpenemisriski helteellä. Kosteus nousee linkoamisen aikoihin. Painon nopea nousu = satohuippu. | src:PES1 |
| **Syksy** | [vko 36-48] Painon stabiloituminen syysruokinnan jälkeen. Kosteus seurannassa (homevaara märässä syksyssä). | src:PES1 |
| **Talvi** | Jatkuva painonseuranta (0.3 kg/vko normaali). Ydinlämpö >20°C. Kosteus 50-70%. | src:PES1 |

## Virhe- ja vaaratilanteet

### ⚠️ Anturi ei lähetä dataa
- **Havaitseminen:** Ei dataa >1h
- **Toimenpide:** Tarkista akku, BLE-yhteys, ilmoita laitehuoltajalle
- **Lähde:** src:PES1

### ⚠️ Lämpötila putoaa äkisti
- **Havaitseminen:** >5°C pudotus 2h sisällä
- **Toimenpide:** HÄLYTYS tarhaajalle: mahdollinen emokato tai parveilu
- **Lähde:** src:PES1

## Epävarmuudet
- Anturin sijainti pesässä vaikuttaa lukemin — reuna vs. keskusta voi erota 10°C.

## Lähteet
- **src:PES1**: SML / Arnia Ltd — *Pesänseurantatekniikka* (2024) https://www.arnia.co.uk/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on sikiöpesän normaalilämpötila?**
    → `DECISION_METRICS_AND_THRESHOLDS.brood_nest_temp_c.value` [src:PES1]
 2. **Mikä on talvipallon kriittinen alaraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_c.action` [src:PES1]
 3. **Mikä on kosteuteen normaali vaihteluväli?**
    → `DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.value` [src:PES1]
 4. **Mikä on normaali talvinen painohäviö per viikko?**
    → `DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.value` [src:PES1]
 5. **Mikä äkillinen painonpudotus tarkoittaa?**
    → `DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action` [src:PES1]
 6. **Kuinka usein anturia luetaan?**
    → `PROCESS_FLOWS.monitoring.steps` [src:PES1]
 7. **Mitä >80% kosteus aiheuttaa?**
    → `DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action` [src:PES1]
 8. **Miten painon nousu keväällä tulkitaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.spring_weight_increase_start.value` [src:PES1]
 9. **Mitä tapahtuu anturikatossa?**
    → `FAILURE_MODES[0].action` [src:PES1]
10. **Mikä laukaisee lämpötilahälytyksen?**
    → `FAILURE_MODES[1].detection` [src:PES1]
11. **Mikä on sikiövaurion lämpötilaraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.brood_nest_temp_c.action` [src:PES1]
12. **Kenelle painohälytyksestä ilmoitetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action` [src:PES1]
13. **Mikä on ylilämpenemisriski kesällä?**
    → `SEASONAL_RULES[1].action` [src:PES1]
14. **Miten syysruokinnan onnistuminen näkyy painossa?**
    → `SEASONAL_RULES[2].action` [src:PES1]
15. **Mikä on normaali talvipallon lämpötilaväli?**
    → `DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_c.value` [src:PES1]
16. **Miten painonpudotusraja lasketaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.action` [src:PES1]
17. **Mitä äkillinen lämpötilan lasku voi tarkoittaa?**
    → `FAILURE_MODES[1].action` [src:PES1]
18. **Mikä on satohuipun painosignaali?**
    → `SEASONAL_RULES[1].action` [src:PES1]
19. **Miten anturin paikka vaikuttaa lukemiin?**
    → `UNCERTAINTY_NOTES` [src:PES1]
20. **Mikä on kuivuusstressin kosteusraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action` [src:PES1]
21. **Mikä on viikkoraportin sisältö?**
    → `PROCESS_FLOWS.monitoring.steps` [src:PES1]
22. **Miten homevaara tunnistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.action` [src:PES1]
23. **Mikä on BLE-yhteyden tarkistustapa?**
    → `FAILURE_MODES[0].action` [src:PES1]
24. **Kuinka usein data tallennetaan?**
    → `PROCESS_FLOWS.monitoring.steps` [src:PES1]
25. **Miten keväällä lämpötilaa seurataan?**
    → `SEASONAL_RULES[0].action` [src:PES1]
26. **Mikä on painon nousun merkitys keväällä?**
    → `SEASONAL_RULES[0].action` [src:PES1]
27. **Mikä on kriittisen talvinen viikkohäviö?**
    → `DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.action` [src:PES1]
28. **Miten ryöstö näkyy painossa?**
    → `DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.action` [src:PES1]
29. **Kenelle anturivika ilmoitetaan?**
    → `FAILURE_MODES[0].action` [src:PES1]
30. **Mikä on datan hälytysviive?**
    → `FAILURE_MODES[0].detection` [src:PES1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 16
## AGENT 16: Nektari-informaatikko
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: nektari_informaatikko
  agent_name: Nektari-informaatikko
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Yhdistää fenologin, hortonomin ja lentosään datat satoennusteeksi
- 'Pääsatokasvit: maitohorsma, vadelma, apilat, rypsi, lehmus'
DECISION_METRICS_AND_THRESHOLDS:
  nectar_flow_start_indicator:
    value: Painonnousu >0.5 kg/pv + T >18°C + fenologinen kynnys → satokausi käynnissä
    source: src:NEK1
  peak_flow_rate_kg_per_day:
    value: 2-5 kg/pv pesää kohti → huippusato (maitohorsma)
    source: src:NEK1
  flow_end_indicator:
    value: Painonnousu <0.2 kg/pv 3 peräkkäisenä pv → satokausi hiipuu
    source: src:NEK1
  super_addition_trigger:
    value: ≥75% kehyksistä täynnä → lisää korotus HETI
    source: src:NEK1
  honey_moisture_check:
    value: Refraktometri <18% → linkoamiskelpoinen
    source: src:NEK1
  daily_weight_gain_kg:
    value: Seuranta puntaripesällä
    action: '>0.5 kg/pv + T>18°C → satokausi ALKAA, aseta korotukset. <0.2 kg/pv 3
      pv → satokausi HIIPUU.'
    source: src:NEK1
  peak_flow_kg_day:
    value: Maitohorsma 2-5 kg/pv, rypsi 1-3 kg/pv, lehmus 1-3 kg/pv
    action: '>3 kg/pv → tarkista korotustila, lisää jos ≥75% kehyksistä täynnä.'
    source: src:NEK1
  moisture_content_pct:
    value: 18
    action: <18% → linkoamiskelpoinen (refraktometri). >20% → EI linkoa, anna kypsyä.
      Rypsi >19% → kiteytymisriski, linkoa HETI.
    source: src:NEK1
  nectar_secretion_conditions:
    value: T>15°C + RH 50-80% + aurinkoista
    action: Optimaaliolosuhteet → ilmoita tarhaajalle satokauden alkamisesta. T<13°C
      tai RH<40% → eritys pysähtyy.
    source: src:NEK1
  season_end_trigger:
    value: Painonlisäys <0.2 kg/pv + maitohorsma kukkinut → satokausi ohi
    action: 'Ilmoita tarhaajalle: aloita linkoaminen ja syysruokintasuunnittelu vko
      32-34.'
    source: src:NEK1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Seuraa pajun kukintaa → ensimmäinen nektarivirtaus. Ei vielä
    satoa.'
  source: src:NEK1
- season: Kesä
  action: '[vko 22-35] Rypsi- ja vadelmavirtaus. Korotusten lisäys ajoissa. Linkoaminen
    rypsin jälkeen (kiteytymisriski).'
  source: src:NEK1
- season: Loppukesä
  action: '[vko 22-35] Maitohorsma = pääsato. Seuraa painoa päivittäin. Viimeinen
    linkoaminen elo-syyskuussa.'
  source: src:NEK1
- season: Syksy-talvi
  action: '[vko 36-48] Ei nektarivirtausta. Varastojen seuranta.'
  source: src:NEK1
FAILURE_MODES:
- mode: Satokausi jää lyhyeksi (kuivuus/kylmyys)
  detection: Painonnousu <50% edellisen vuoden vastaavaan jaksoon verrattuna
  action: 'Varoita tarhaajaa: ruokintamäärän nosto syksyllä'
  source: src:NEK1
- mode: Rypsin nopea kiteytyminen kehyksissä
  detection: Rypsi kukkii + hunaja kehyksissä paksua/vaaleaa
  action: Linkoa pikaisesti ennen kiteytymistä
  source: src:NEK1
PROCESS_FLOWS:
- flow_id: FLOW_NEKT_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Seuraa pajun kukintaa → ensimmäinen nektarivirtaus. Ei vielä
    satoa.'
  output: Tarkistuslista
  source: src:NEKT
- flow_id: FLOW_NEKT_03
  trigger: 'Havaittu: Satokausi jää lyhyeksi (kuivuus/kylmyys)'
  action: 'Varoita tarhaajaa: ruokintamäärän nosto syksyllä'
  output: Poikkeamaraportti
  source: src:NEKT
- flow_id: FLOW_NEKT_04
  trigger: Säännöllinen heartbeat
  action: 'nektari_informaatikko: rutiiniarviointi'
  output: Status-raportti
  source: src:NEKT
KNOWLEDGE_TABLES:
  nectar_sources:
  - plant: Pajut
    period: Huhti-touko
    type: Kevätravinto
    flow_kg_day: 0.1-0.3
    source: src:NEK1
  - plant: Rypsi
    period: Kesäkuu
    type: Pääsato (peltoalue)
    flow_kg_day: 1-3
    source: src:NEK1
  - plant: Vadelma
    period: Kesä-heinäkuu
    type: Pääsato (metsäreuna)
    flow_kg_day: 1-2
    source: src:NEK1
  - plant: Maitohorsma
    period: Heinä-elokuu
    type: Pääsato (hakkuualueet)
    flow_kg_day: 2-5
    source: src:NEK1
  - plant: Lehmus
    period: Heinäkuu
    type: Kaupunkisato
    flow_kg_day: 1-3
    source: src:NEK1
  - plant: Apilat
    period: Kesä-elokuu
    type: Jatkuva täydennys
    flow_kg_day: 0.5-1.5
    source: src:NEK1
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Nektarieritys on voimakkaasti sääriippuvaista — kuivuus voi nollata sadon.
- Pesäkohtaiset tuotantoerot voivat olla 50-100%.
SOURCE_REGISTRY:
  sources:
  - id: src:NEK1
    org: SML
    title: Mehiläishoitoa käytännössä + satokasvitiedot
    year: 2011
    url: null
    identifier: ISBN 978-952-92-9184-4
    supports: Satokasvit, nektarivirtaus, linkoaminen.
eval_questions:
- q: Mikä on nektarivirtauksen aloitusindikaattori?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nectar_flow_start_indicator.value
  source: src:NEK1
- q: Mikä on huippusadon päivätaso?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.peak_flow_rate_kg_per_day.value
  source: src:NEK1
- q: Milloin satokausi hiipuu?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.flow_end_indicator.value
  source: src:NEK1
- q: Milloin korotus lisätään?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.super_addition_trigger.value
  source: src:NEK1
- q: Mikä on hunajan linkoamiskelpoinen kosteus?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.honey_moisture_check.value
  source: src:NEK1
- q: Mikä on maitohorsman satokausi?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[3].period
  source: src:NEK1
- q: Mikä on maitohorsman tuotto kg/pv?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[3].flow_kg_day
  source: src:NEK1
- q: Miksi rypsi linkotaan nopeasti?
  a_ref: FAILURE_MODES[1].action
  source: src:NEK1
- q: Mitä tehdään heikon sadon jälkeen?
  a_ref: FAILURE_MODES[0].action
  source: src:NEK1
- q: Milloin viimeinen linkoaminen on?
  a_ref: SEASONAL_RULES[2].action
  source: src:NEK1
- q: Mikä on rypsin satokausi?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[1].period
  source: src:NEK1
- q: Mikä on lehmuksen tuotto?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[4].flow_kg_day
  source: src:NEK1
- q: Milloin pajun nektari virtaa?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[0].period
  source: src:NEK1
- q: Mikä on apilan rooli?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[5].type
  source: src:NEK1
- q: Miten kuivuus vaikuttaa satoon?
  a_ref: UNCERTAINTY_NOTES
  source: src:NEK1
- q: Miten heikko satokausi havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:NEK1
- q: Mikä on vadelma-sadon ajankohta?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[2].period
  source: src:NEK1
- q: Miten rypsin kiteytyminen tunnistetaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:NEK1
- q: Mikä on pajun sadon merkitys?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[0].type
  source: src:NEK1
- q: Mikä on vadelma-virtauksen kg/pv?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[2].flow_kg_day
  source: src:NEK1
- q: Miten painonseurantaa käytetään satoennusteessa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nectar_flow_start_indicator.value
  source: src:NEK1
- q: Mitkä ovat rypsin kiteytymisen merkit?
  a_ref: FAILURE_MODES[1].detection
  source: src:NEK1
- q: Milloin lehmussato on?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[4].period
  source: src:NEK1
- q: Mikä on satokauden pituus tyypillisesti?
  a_ref: SEASONAL_RULES
  source: src:NEK1
- q: Kenelle satokauden loppumisesta ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:NEK1
- q: Kuinka suuri pesäkohtainen tuotantoero voi olla?
  a_ref: UNCERTAINTY_NOTES
  source: src:NEK1
- q: Mikä on refraktometrin käyttötarkoitus?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.honey_moisture_check.value
  source: src:NEK1
- q: Miten korotuspäätös tehdään?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.super_addition_trigger.value
  source: src:NEK1
- q: Mikä on apilan tuotto?
  a_ref: KNOWLEDGE_TABLES.nectar_sources[5].flow_kg_day
  source: src:NEK1
- q: Miten sääriippuvuus vaikuttaa suunnitteluun?
  a_ref: UNCERTAINTY_NOTES
  source: src:NEK1
- q: 'Operatiivinen päätöskysymys #1?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #2?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #3?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #4?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #5?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #6?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #7?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #8?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #9?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
- q: 'Operatiivinen päätöskysymys #10?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:NEKT
```

**sources.yaml:**
```yaml
sources:
- id: src:NEK1
  org: SML
  title: Mehiläishoitoa käytännössä + satokasvitiedot
  year: 2011
  url: null
  identifier: ISBN 978-952-92-9184-4
  supports: Satokasvit, nektarivirtaus, linkoaminen.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Nektari-informaatikko
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Yhdistää fenologin, hortonomin ja lentosään datat satoennusteeksi
- Pääsatokasvit: maitohorsma, vadelma, apilat, rypsi, lehmus

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| nectar_flow_start_indicator | Painonnousu >0.5 kg/pv + T >18°C + fenologinen kynnys → satokausi käynnissä | — | src:NEK1 |
| peak_flow_rate_kg_per_day | 2-5 kg/pv pesää kohti → huippusato (maitohorsma) | — | src:NEK1 |
| flow_end_indicator | Painonnousu <0.2 kg/pv 3 peräkkäisenä pv → satokausi hiipuu | — | src:NEK1 |
| super_addition_trigger | ≥75% kehyksistä täynnä → lisää korotus HETI | — | src:NEK1 |
| honey_moisture_check | Refraktometri <18% → linkoamiskelpoinen | — | src:NEK1 |
| daily_weight_gain_kg | Seuranta puntaripesällä | >0.5 kg/pv + T>18°C → satokausi ALKAA, aseta korotukset. <0.2 kg/pv 3 pv → satokausi HIIPUU. | src:NEK1 |
| peak_flow_kg_day | Maitohorsma 2-5 kg/pv, rypsi 1-3 kg/pv, lehmus 1-3 kg/pv | >3 kg/pv → tarkista korotustila, lisää jos ≥75% kehyksistä täynnä. | src:NEK1 |
| moisture_content_pct | 18 | <18% → linkoamiskelpoinen (refraktometri). >20% → EI linkoa, anna kypsyä. Rypsi >19% → kiteytymisriski, linkoa HETI. | src:NEK1 |
| nectar_secretion_conditions | T>15°C + RH 50-80% + aurinkoista | Optimaaliolosuhteet → ilmoita tarhaajalle satokauden alkamisesta. T<13°C tai RH<40% → eritys pysähtyy. | src:NEK1 |
| season_end_trigger | Painonlisäys <0.2 kg/pv + maitohorsma kukkinut → satokausi ohi | Ilmoita tarhaajalle: aloita linkoaminen ja syysruokintasuunnittelu vko 32-34. | src:NEK1 |

## Tietotaulukot

**nectar_sources:**

| plant | period | type | flow_kg_day | source |
| --- | --- | --- | --- | --- |
| Pajut | Huhti-touko | Kevätravinto | 0.1-0.3 | src:NEK1 |
| Rypsi | Kesäkuu | Pääsato (peltoalue) | 1-3 | src:NEK1 |
| Vadelma | Kesä-heinäkuu | Pääsato (metsäreuna) | 1-2 | src:NEK1 |
| Maitohorsma | Heinä-elokuu | Pääsato (hakkuualueet) | 2-5 | src:NEK1 |
| Lehmus | Heinäkuu | Kaupunkisato | 1-3 | src:NEK1 |
| Apilat | Kesä-elokuu | Jatkuva täydennys | 0.5-1.5 | src:NEK1 |

## Prosessit

**FLOW_NEKT_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Seuraa pajun kukintaa → ensimmäinen nektarivirtaus. Ei vielä satoa.
  Tulos: Tarkistuslista

**FLOW_NEKT_03:** Havaittu: Satokausi jää lyhyeksi (kuivuus/kylmyys)
  → Varoita tarhaajaa: ruokintamäärän nosto syksyllä
  Tulos: Poikkeamaraportti

**FLOW_NEKT_04:** Säännöllinen heartbeat
  → nektari_informaatikko: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Seuraa pajun kukintaa → ensimmäinen nektarivirtaus. Ei vielä satoa. | src:NEK1 |
| **Kesä** | [vko 22-35] Rypsi- ja vadelmavirtaus. Korotusten lisäys ajoissa. Linkoaminen rypsin jälkeen (kiteytymisriski). | src:NEK1 |
| **Loppukesä** | [vko 22-35] Maitohorsma = pääsato. Seuraa painoa päivittäin. Viimeinen linkoaminen elo-syyskuussa. | src:NEK1 |
| **Syksy-talvi** | [vko 36-48] Ei nektarivirtausta. Varastojen seuranta. | src:NEK1 |

## Virhe- ja vaaratilanteet

### ⚠️ Satokausi jää lyhyeksi (kuivuus/kylmyys)
- **Havaitseminen:** Painonnousu <50% edellisen vuoden vastaavaan jaksoon verrattuna
- **Toimenpide:** Varoita tarhaajaa: ruokintamäärän nosto syksyllä
- **Lähde:** src:NEK1

### ⚠️ Rypsin nopea kiteytyminen kehyksissä
- **Havaitseminen:** Rypsi kukkii + hunaja kehyksissä paksua/vaaleaa
- **Toimenpide:** Linkoa pikaisesti ennen kiteytymistä
- **Lähde:** src:NEK1

## Epävarmuudet
- Nektarieritys on voimakkaasti sääriippuvaista — kuivuus voi nollata sadon.
- Pesäkohtaiset tuotantoerot voivat olla 50-100%.

## Lähteet
- **src:NEK1**: SML — *Mehiläishoitoa käytännössä + satokasvitiedot* (2011) —

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on nektarivirtauksen aloitusindikaattori?**
    → `DECISION_METRICS_AND_THRESHOLDS.nectar_flow_start_indicator.value` [src:NEK1]
 2. **Mikä on huippusadon päivätaso?**
    → `DECISION_METRICS_AND_THRESHOLDS.peak_flow_rate_kg_per_day.value` [src:NEK1]
 3. **Milloin satokausi hiipuu?**
    → `DECISION_METRICS_AND_THRESHOLDS.flow_end_indicator.value` [src:NEK1]
 4. **Milloin korotus lisätään?**
    → `DECISION_METRICS_AND_THRESHOLDS.super_addition_trigger.value` [src:NEK1]
 5. **Mikä on hunajan linkoamiskelpoinen kosteus?**
    → `DECISION_METRICS_AND_THRESHOLDS.honey_moisture_check.value` [src:NEK1]
 6. **Mikä on maitohorsman satokausi?**
    → `KNOWLEDGE_TABLES.nectar_sources[3].period` [src:NEK1]
 7. **Mikä on maitohorsman tuotto kg/pv?**
    → `KNOWLEDGE_TABLES.nectar_sources[3].flow_kg_day` [src:NEK1]
 8. **Miksi rypsi linkotaan nopeasti?**
    → `FAILURE_MODES[1].action` [src:NEK1]
 9. **Mitä tehdään heikon sadon jälkeen?**
    → `FAILURE_MODES[0].action` [src:NEK1]
10. **Milloin viimeinen linkoaminen on?**
    → `SEASONAL_RULES[2].action` [src:NEK1]
11. **Mikä on rypsin satokausi?**
    → `KNOWLEDGE_TABLES.nectar_sources[1].period` [src:NEK1]
12. **Mikä on lehmuksen tuotto?**
    → `KNOWLEDGE_TABLES.nectar_sources[4].flow_kg_day` [src:NEK1]
13. **Milloin pajun nektari virtaa?**
    → `KNOWLEDGE_TABLES.nectar_sources[0].period` [src:NEK1]
14. **Mikä on apilan rooli?**
    → `KNOWLEDGE_TABLES.nectar_sources[5].type` [src:NEK1]
15. **Miten kuivuus vaikuttaa satoon?**
    → `UNCERTAINTY_NOTES` [src:NEK1]
16. **Miten heikko satokausi havaitaan?**
    → `FAILURE_MODES[0].detection` [src:NEK1]
17. **Mikä on vadelma-sadon ajankohta?**
    → `KNOWLEDGE_TABLES.nectar_sources[2].period` [src:NEK1]
18. **Miten rypsin kiteytyminen tunnistetaan?**
    → `FAILURE_MODES[1].detection` [src:NEK1]
19. **Mikä on pajun sadon merkitys?**
    → `KNOWLEDGE_TABLES.nectar_sources[0].type` [src:NEK1]
20. **Mikä on vadelma-virtauksen kg/pv?**
    → `KNOWLEDGE_TABLES.nectar_sources[2].flow_kg_day` [src:NEK1]
21. **Miten painonseurantaa käytetään satoennusteessa?**
    → `DECISION_METRICS_AND_THRESHOLDS.nectar_flow_start_indicator.value` [src:NEK1]
22. **Mitkä ovat rypsin kiteytymisen merkit?**
    → `FAILURE_MODES[1].detection` [src:NEK1]
23. **Milloin lehmussato on?**
    → `KNOWLEDGE_TABLES.nectar_sources[4].period` [src:NEK1]
24. **Mikä on satokauden pituus tyypillisesti?**
    → `SEASONAL_RULES` [src:NEK1]
25. **Kenelle satokauden loppumisesta ilmoitetaan?**
    → `FAILURE_MODES[0].action` [src:NEK1]
26. **Kuinka suuri pesäkohtainen tuotantoero voi olla?**
    → `UNCERTAINTY_NOTES` [src:NEK1]
27. **Mikä on refraktometrin käyttötarkoitus?**
    → `DECISION_METRICS_AND_THRESHOLDS.honey_moisture_check.value` [src:NEK1]
28. **Miten korotuspäätös tehdään?**
    → `DECISION_METRICS_AND_THRESHOLDS.super_addition_trigger.value` [src:NEK1]
29. **Mikä on apilan tuotto?**
    → `KNOWLEDGE_TABLES.nectar_sources[5].flow_kg_day` [src:NEK1]
30. **Miten sääriippuvuus vaikuttaa suunnitteluun?**
    → `UNCERTAINTY_NOTES` [src:NEK1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 17
## AGENT 17: Tautivahti (mehiläiset)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: tautivahti
  agent_name: Tautivahti (mehiläiset)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Seuraa kaikkien tarhojen tautitilannetta
- Kytkentä tarhaajaan, entomologiin ja Ruokavirastoon
DECISION_METRICS_AND_THRESHOLDS:
  afb_zero_tolerance:
    value: Yksikin AFB-epäily → HETI Ruokavirasto + eristys
    source: src:TAU1
  efb_threshold:
    value: Epäily → näytteenotto, torjuntatoimet
    source: src:TAU1
  nosema_spore_count:
    value: '>1 milj. itiötä/mehiläinen → hoitotarve'
    source: src:TAU1
  chalkbrood_frame_pct:
    value: 10
    action: '>10% kehyksistä kalkkisikiötä → vaihda emo, paranna ilmanvaihtoa, poista
      pahimmat kehykset'
    source: src:TAU1
  deformed_wing_virus_signs:
    value: Surkastuneet siivet nuorilla mehiläisillä → osoittaa vakavaa varroa-infektiota
    action: Välitön varroa-hoito
    source: src:TAU1
  afb_tolerance:
    value: 0
    action: 'AFB: NOLLATOLERANSSI → ilmoita Ruokavirasto 029 530 0400, eristä tarha,
      ÄLÄ siirrä kehyksiä'
    source: src:TAU1
  efb_detection:
    value: Mosaiikkimainen sikiöpeite, kellertävät toukat
    action: EFB-epäily → näytteenotto Ruokavirastolle, eristä pesä
    source: src:TAU1
  nosema_spores_per_bee:
    value: 1000000
    action: '>1 milj. itiötä/mehiläinen → fumagilliinikiellon takia hoito oksa- tai
      etikkahapolla'
    source: src:TAU1
  dwv_detection:
    value: Surkastuneet siivet kuoriutuvilla mehiläisillä
    action: DWV havaittu → välitön varroa-mittaus. >3/100 → kemiallinen hoito vaikka
      satokausi.
    source: src:TAU1
SEASONAL_RULES:
- season: Kevät
  action: 'Ensitarkistus vko 16-18: sikiöpeite, ruokavarasto >5 kg. Nosema-näyte 30
    mehiläisestä jos epäily. Kalkkisikiön tarkistus.'
  source: src:TAU1
- season: Kesä
  action: '[vko 22-35] AFB-tarkistus jokaisella satokehysten käsittelyllä. Siirtoihin
    ei tartuntatarhan kehyksiä. DWV-seuranta.'
  source: src:TAU1
- season: Syksy
  action: 'Varroa-hoito elokuussa hunajankorjuun jälkeen: oksaalihappo tai amitraz.
    Jos >3/100 → toinen kierros syyskuussa.'
  source: src:TAU1
- season: Talvi
  action: 'Oksaalihappohoito joulukuussa (sikiöttömään aikaan, T<5°C). Kuolleisuusseuranta:
    >30% → dokumentoi, tarkista varroa+nosema.'
  source: src:TAU1
FAILURE_MODES:
- mode: AFB-löydös
  detection: Tikkulankatesti positiivinen + haju
  action: ÄLÄ siirrä kehyksiä. Ilmoita Ruokavirastolle 029 530 0400. Eristä tarha.
  source: src:TAU1
- mode: Massakuolema
  detection: '>30% pesistä kuollut samalla tarhalla'
  action: Tarkista myrkytys, varroa, nosema. Kerää näytteet. Ilmoita Ruokavirastolle.
  source: src:TAU1
PROCESS_FLOWS:
- flow_id: FLOW_TAUT_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: 'Ensitarkistus vko 16-18: sikiöpeite, ruokavarasto >5 kg. Nosema-näyte 30
    mehiläisestä jos epäily. Ka'
  output: Tarkistuslista
  source: src:TAUT
- flow_id: FLOW_TAUT_03
  trigger: 'Havaittu: AFB-löydös'
  action: ÄLÄ siirrä kehyksiä. Ilmoita Ruokavirastolle 029 530 0400. Eristä tarha.
  output: Poikkeamaraportti
  source: src:TAUT
- flow_id: FLOW_TAUT_04
  trigger: Säännöllinen heartbeat
  action: 'tautivahti: rutiiniarviointi'
  output: Status-raportti
  source: src:TAUT
KNOWLEDGE_TABLES:
  diseases:
  - disease: AFB
    pathogen: Paenibacillus larvae
    status: Valvottava eläintauti
    action: Ilmoitus + eristys + viranomaisohje
    source: src:TAU1
  - disease: EFB
    pathogen: Melissococcus plutonius
    status: Ei valvottava
    action: Näytteenotto, torjuntatoimet
    source: src:TAU1
  - disease: Nosema
    pathogen: Nosema ceranae / N. apis
    status: Yleinen
    action: Pesän vahvistaminen, keväällä emonvaihto
    source: src:TAU1
  - disease: Kalkkisikiö
    pathogen: Ascosphaera apis
    status: Yleinen
    action: Ilmanvaihto, heikon emon vaihto
    source: src:TAU1
  - disease: DWV (siipiepämuodostuma)
    pathogen: Deformed Wing Virus
    status: Liittyy varroaan
    action: Varroa-hoito
    source: src:TAU1
COMPLIANCE_AND_LEGAL:
  afb: AFB on valvottava eläintauti — ilmoitus lakisääteisesti pakollinen [src:TAU1]
  veterinary: Lääkinnälliset valmisteet vain eläinlääkärin luvalla tai Ruokaviraston
    hyväksyminä [src:TAU1]
UNCERTAINTY_NOTES:
- Nosema-itiömäärän kynnysarvo vaihtelee lähteittäin 0.5-2 milj.
- AFB voi olla piilevänä pitkään ennen kliinisiä oireita.
SOURCE_REGISTRY:
  sources:
  - id: src:TAU1
    org: Ruokavirasto
    title: Mehiläisten taudit
    year: 2025
    url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
    supports: AFB, EFB, nosema, kalkkisikiö, DWV.
eval_questions:
- q: Mikä on AFB:n toleranssi?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.afb_zero_tolerance.value
  source: src:TAU1
- q: Miten AFB tunnistetaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:TAU1
- q: Kenelle AFB:stä ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:TAU1
- q: Mikä on noseman hoitoraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.nosema_spore_count.value
  source: src:TAU1
- q: Mitä DWV osoittaa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.deformed_wing_virus_signs.value
  source: src:TAU1
- q: Mikä on kalkkisikiön hälytysraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.chalkbrood_frame_pct.value
  source: src:TAU1
- q: Onko AFB ilmoitusvelvollisuus?
  a_ref: COMPLIANCE_AND_LEGAL.afb
  source: src:TAU1
- q: Miten massakuolema tutkitaan?
  a_ref: FAILURE_MODES[1].action
  source: src:TAU1
- q: Milloin nosema-näyte otetaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:TAU1
- q: Mitä DWV-löydökselle tehdään?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.deformed_wing_virus_signs.action
  source: src:TAU1
- q: Miten EFB eroaa AFB:stä valvonnan osalta?
  a_ref: KNOWLEDGE_TABLES.diseases[1].status
  source: src:TAU1
- q: Mikä on noseman aiheuttaja?
  a_ref: KNOWLEDGE_TABLES.diseases[2].pathogen
  source: src:TAU1
- q: Mikä aiheuttaa kalkkisikiön?
  a_ref: KNOWLEDGE_TABLES.diseases[3].pathogen
  source: src:TAU1
- q: Milloin AFB/EFB-tarkkailu on tärkeintä?
  a_ref: SEASONAL_RULES[1].action
  source: src:TAU1
- q: Mitä massakuolema tarkoittaa?
  a_ref: FAILURE_MODES[1].detection
  source: src:TAU1
- q: Mikä on Ruokaviraston puhelinnumero?
  a_ref: FAILURE_MODES[0].action
  source: src:TAU1
- q: Miten kalkkisikiö hoidetaan?
  a_ref: KNOWLEDGE_TABLES.diseases[3].action
  source: src:TAU1
- q: Saako AFB-pesän kehyksiä kierrättää?
  a_ref: FAILURE_MODES[0].action
  source: src:TAU1
- q: Mitä kesällä seurataan taudeista?
  a_ref: SEASONAL_RULES[1].action
  source: src:TAU1
- q: Miten syksyn kuolleet pesät käsitellään?
  a_ref: SEASONAL_RULES[2].action
  source: src:TAU1
- q: Vaatiiko lääkinnällinen hoito lupaa?
  a_ref: COMPLIANCE_AND_LEGAL.veterinary
  source: src:TAU1
- q: Voiko AFB olla piilevä?
  a_ref: UNCERTAINTY_NOTES
  source: src:TAU1
- q: Miten nosemaa hoidetaan?
  a_ref: KNOWLEDGE_TABLES.diseases[2].action
  source: src:TAU1
- q: Onko EFB valvottava?
  a_ref: KNOWLEDGE_TABLES.diseases[1].status
  source: src:TAU1
- q: Mikä aiheuttaa DWV:n?
  a_ref: KNOWLEDGE_TABLES.diseases[4].pathogen
  source: src:TAU1
- q: Mihin DWV liittyy?
  a_ref: KNOWLEDGE_TABLES.diseases[4].status
  source: src:TAU1
- q: Mikä on tikkulankatesti?
  a_ref: FAILURE_MODES[0].detection
  source: src:TAU1
- q: Milloin talvikuolleisuutta seurataan?
  a_ref: SEASONAL_RULES[3].action
  source: src:TAU1
- q: Vaihteleeko nosema-kynnys?
  a_ref: UNCERTAINTY_NOTES
  source: src:TAU1
- q: Kenelle massakuolemasta ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:TAU1
- q: 'Operatiivinen päätöskysymys #1?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #2?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #3?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #4?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #5?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #6?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #7?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #8?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #9?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
- q: 'Operatiivinen päätöskysymys #10?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:TAUT
```

**sources.yaml:**
```yaml
sources:
- id: src:TAU1
  org: Ruokavirasto
  title: Mehiläisten taudit
  year: 2025
  url: https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/
  supports: AFB, EFB, nosema, kalkkisikiö, DWV.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Tautivahti (mehiläiset)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Seuraa kaikkien tarhojen tautitilannetta
- Kytkentä tarhaajaan, entomologiin ja Ruokavirastoon

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| afb_zero_tolerance | Yksikin AFB-epäily → HETI Ruokavirasto + eristys | — | src:TAU1 |
| efb_threshold | Epäily → näytteenotto, torjuntatoimet | — | src:TAU1 |
| nosema_spore_count | >1 milj. itiötä/mehiläinen → hoitotarve | — | src:TAU1 |
| chalkbrood_frame_pct | 10 | >10% kehyksistä kalkkisikiötä → vaihda emo, paranna ilmanvaihtoa, poista pahimmat kehykset | src:TAU1 |
| deformed_wing_virus_signs | Surkastuneet siivet nuorilla mehiläisillä → osoittaa vakavaa varroa-infektiota | Välitön varroa-hoito | src:TAU1 |
| afb_tolerance | 0 | AFB: NOLLATOLERANSSI → ilmoita Ruokavirasto 029 530 0400, eristä tarha, ÄLÄ siirrä kehyksiä | src:TAU1 |
| efb_detection | Mosaiikkimainen sikiöpeite, kellertävät toukat | EFB-epäily → näytteenotto Ruokavirastolle, eristä pesä | src:TAU1 |
| nosema_spores_per_bee | 1000000 | >1 milj. itiötä/mehiläinen → fumagilliinikiellon takia hoito oksa- tai etikkahapolla | src:TAU1 |
| dwv_detection | Surkastuneet siivet kuoriutuvilla mehiläisillä | DWV havaittu → välitön varroa-mittaus. >3/100 → kemiallinen hoito vaikka satokausi. | src:TAU1 |

## Tietotaulukot

**diseases:**

| disease | pathogen | status | action | source |
| --- | --- | --- | --- | --- |
| AFB | Paenibacillus larvae | Valvottava eläintauti | Ilmoitus + eristys + viranomaisohje | src:TAU1 |
| EFB | Melissococcus plutonius | Ei valvottava | Näytteenotto, torjuntatoimet | src:TAU1 |
| Nosema | Nosema ceranae / N. apis | Yleinen | Pesän vahvistaminen, keväällä emonvaihto | src:TAU1 |
| Kalkkisikiö | Ascosphaera apis | Yleinen | Ilmanvaihto, heikon emon vaihto | src:TAU1 |
| DWV (siipiepämuodostuma) | Deformed Wing Virus | Liittyy varroaan | Varroa-hoito | src:TAU1 |

## Prosessit

**FLOW_TAUT_02:** Kausi vaihtuu: Kevät
  → Ensitarkistus vko 16-18: sikiöpeite, ruokavarasto >5 kg. Nosema-näyte 30 mehiläisestä jos epäily. Ka
  Tulos: Tarkistuslista

**FLOW_TAUT_03:** Havaittu: AFB-löydös
  → ÄLÄ siirrä kehyksiä. Ilmoita Ruokavirastolle 029 530 0400. Eristä tarha.
  Tulos: Poikkeamaraportti

**FLOW_TAUT_04:** Säännöllinen heartbeat
  → tautivahti: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | Ensitarkistus vko 16-18: sikiöpeite, ruokavarasto >5 kg. Nosema-näyte 30 mehiläisestä jos epäily. Kalkkisikiön tarkistus. | src:TAU1 |
| **Kesä** | [vko 22-35] AFB-tarkistus jokaisella satokehysten käsittelyllä. Siirtoihin ei tartuntatarhan kehyksiä. DWV-seuranta. | src:TAU1 |
| **Syksy** | Varroa-hoito elokuussa hunajankorjuun jälkeen: oksaalihappo tai amitraz. Jos >3/100 → toinen kierros syyskuussa. | src:TAU1 |
| **Talvi** | Oksaalihappohoito joulukuussa (sikiöttömään aikaan, T<5°C). Kuolleisuusseuranta: >30% → dokumentoi, tarkista varroa+nosema. | src:TAU1 |

## Virhe- ja vaaratilanteet

### ⚠️ AFB-löydös
- **Havaitseminen:** Tikkulankatesti positiivinen + haju
- **Toimenpide:** ÄLÄ siirrä kehyksiä. Ilmoita Ruokavirastolle 029 530 0400. Eristä tarha.
- **Lähde:** src:TAU1

### ⚠️ Massakuolema
- **Havaitseminen:** >30% pesistä kuollut samalla tarhalla
- **Toimenpide:** Tarkista myrkytys, varroa, nosema. Kerää näytteet. Ilmoita Ruokavirastolle.
- **Lähde:** src:TAU1

## Lait ja vaatimukset
- **afb:** AFB on valvottava eläintauti — ilmoitus lakisääteisesti pakollinen [src:TAU1]
- **veterinary:** Lääkinnälliset valmisteet vain eläinlääkärin luvalla tai Ruokaviraston hyväksyminä [src:TAU1]

## Epävarmuudet
- Nosema-itiömäärän kynnysarvo vaihtelee lähteittäin 0.5-2 milj.
- AFB voi olla piilevänä pitkään ennen kliinisiä oireita.

## Lähteet
- **src:TAU1**: Ruokavirasto — *Mehiläisten taudit* (2025) https://www.ruokavirasto.fi/elaimet/elainterveys-ja-elaintaudit/elaintaudit/mehilaiset/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on AFB:n toleranssi?**
    → `DECISION_METRICS_AND_THRESHOLDS.afb_zero_tolerance.value` [src:TAU1]
 2. **Miten AFB tunnistetaan?**
    → `FAILURE_MODES[0].detection` [src:TAU1]
 3. **Kenelle AFB:stä ilmoitetaan?**
    → `FAILURE_MODES[0].action` [src:TAU1]
 4. **Mikä on noseman hoitoraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.nosema_spore_count.value` [src:TAU1]
 5. **Mitä DWV osoittaa?**
    → `DECISION_METRICS_AND_THRESHOLDS.deformed_wing_virus_signs.value` [src:TAU1]
 6. **Mikä on kalkkisikiön hälytysraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.chalkbrood_frame_pct.value` [src:TAU1]
 7. **Onko AFB ilmoitusvelvollisuus?**
    → `COMPLIANCE_AND_LEGAL.afb` [src:TAU1]
 8. **Miten massakuolema tutkitaan?**
    → `FAILURE_MODES[1].action` [src:TAU1]
 9. **Milloin nosema-näyte otetaan?**
    → `SEASONAL_RULES[0].action` [src:TAU1]
10. **Mitä DWV-löydökselle tehdään?**
    → `DECISION_METRICS_AND_THRESHOLDS.deformed_wing_virus_signs.action` [src:TAU1]
11. **Miten EFB eroaa AFB:stä valvonnan osalta?**
    → `KNOWLEDGE_TABLES.diseases[1].status` [src:TAU1]
12. **Mikä on noseman aiheuttaja?**
    → `KNOWLEDGE_TABLES.diseases[2].pathogen` [src:TAU1]
13. **Mikä aiheuttaa kalkkisikiön?**
    → `KNOWLEDGE_TABLES.diseases[3].pathogen` [src:TAU1]
14. **Milloin AFB/EFB-tarkkailu on tärkeintä?**
    → `SEASONAL_RULES[1].action` [src:TAU1]
15. **Mitä massakuolema tarkoittaa?**
    → `FAILURE_MODES[1].detection` [src:TAU1]
16. **Mikä on Ruokaviraston puhelinnumero?**
    → `FAILURE_MODES[0].action` [src:TAU1]
17. **Miten kalkkisikiö hoidetaan?**
    → `KNOWLEDGE_TABLES.diseases[3].action` [src:TAU1]
18. **Saako AFB-pesän kehyksiä kierrättää?**
    → `FAILURE_MODES[0].action` [src:TAU1]
19. **Mitä kesällä seurataan taudeista?**
    → `SEASONAL_RULES[1].action` [src:TAU1]
20. **Miten syksyn kuolleet pesät käsitellään?**
    → `SEASONAL_RULES[2].action` [src:TAU1]
21. **Vaatiiko lääkinnällinen hoito lupaa?**
    → `COMPLIANCE_AND_LEGAL.veterinary` [src:TAU1]
22. **Voiko AFB olla piilevä?**
    → `UNCERTAINTY_NOTES` [src:TAU1]
23. **Miten nosemaa hoidetaan?**
    → `KNOWLEDGE_TABLES.diseases[2].action` [src:TAU1]
24. **Onko EFB valvottava?**
    → `KNOWLEDGE_TABLES.diseases[1].status` [src:TAU1]
25. **Mikä aiheuttaa DWV:n?**
    → `KNOWLEDGE_TABLES.diseases[4].pathogen` [src:TAU1]
26. **Mihin DWV liittyy?**
    → `KNOWLEDGE_TABLES.diseases[4].status` [src:TAU1]
27. **Mikä on tikkulankatesti?**
    → `FAILURE_MODES[0].detection` [src:TAU1]
28. **Milloin talvikuolleisuutta seurataan?**
    → `SEASONAL_RULES[3].action` [src:TAU1]
29. **Vaihteleeko nosema-kynnys?**
    → `UNCERTAINTY_NOTES` [src:TAU1]
30. **Kenelle massakuolemasta ilmoitetaan?**
    → `FAILURE_MODES[1].action` [src:TAU1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 18
## AGENT 18: Pesäturvallisuuspäällikkö (karhut ym.)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: pesaturvallisuus
  agent_name: Pesäturvallisuuspäällikkö (karhut ym.)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Karhu- ja mäyrävahinkojen ehkäisy mehiläistarhoilla
- Sähköaita ensisijainen suojakeino
- Korvenranta + kaikki muut tarha-sijainnit
DECISION_METRICS_AND_THRESHOLDS:
  electric_fence_voltage_kv:
    value: 4-7 kV (minimi 3 kV, alle → ei estä karhua)
    action: Mittaa vähintään 2x/kk, ennen karhukautta (touko) viikoittain
    source: src:PETU1
  fence_ground_resistance_ohm:
    value: <300 Ω
    action: '>300 Ω → paranna maadoitusta (lisää sauvoja, kastele)'
    source: src:PETU1
  bear_damage_radius_km:
    value: 5
    action: Karhuhavainto <5 km → nosta varotaso, tarkista aidat
    source: src:PETU2
  fence_height_cm:
    value: 90-120
    note: Karhulle riittävä, alin lanka 20 cm maasta
    source: src:PETU1
  grass_under_fence_max_cm:
    value: 10
    action: Ruoho >10 cm → niitä, vuotaa maahan
    source: src:PETU1
SEASONAL_RULES:
- season: Kevät (huhti-touko)
  action: '[vko 14-22] Sähköaidan pystytys/tarkistus ENNEN karhun heräämistä. Jännitemittaus.
    Maadoituksen testaus.'
  source: src:PETU1
- season: Kesä
  action: Ruohon niitto aidan alla 2x/kk. Jännite 2x/kk. Riistanvartijalta karhu-info
    seurantaan.
  source: src:PETU1
- season: Syksy
  action: '[vko 36-48] Aita pidetään päällä lokakuun loppuun. Pesäkohtaiset suojaukset
    talvipesille (hiiri, tikka).'
  source: src:PETU1
- season: Talvi
  action: '[vko 49-13] Aita voidaan poistaa lumitöiden ajaksi (karhu talviunessa).
    Hiiriverkon tarkistus.'
  source: src:PETU1
FAILURE_MODES:
- mode: Karhu murtautunut aitaan
  detection: Kaatuneet pylväät, tuhoutuneet pesät, karhun jäljet
  action: 1. Dokumentoi (valokuva + päiväys), 2. Ilmoita poliisille ja riistanhoitoyhdistykselle,
    3. Hae korvaus Maaseutuvirastosta
  source: src:PETU2
- mode: Aidan jännite liian matala
  detection: Mittari <3 kV
  action: 'Tarkista: ruoho, maadoitus, akku/verkkosyöttö, johdon eristeet'
  source: src:PETU1
PROCESS_FLOWS:
- flow_id: FLOW_PESA_01
  trigger: electric_fence_voltage_kv ylittää kynnysarvon (4-7 kV (minimi 3 kV, alle
    → ei estä karhua))
  action: Mittaa vähintään 2x/kk, ennen karhukautta (touko) viikoittain
  output: Tilanneraportti
  source: src:PESA
- flow_id: FLOW_PESA_02
  trigger: 'Kausi vaihtuu: Kevät (huhti-touko)'
  action: '[vko 14-22] Sähköaidan pystytys/tarkistus ENNEN karhun heräämistä. Jännitemittaus.
    Maadoituksen test'
  output: Tarkistuslista
  source: src:PESA
- flow_id: FLOW_PESA_03
  trigger: 'Havaittu: Karhu murtautunut aitaan'
  action: 1. Dokumentoi (valokuva + päiväys), 2. Ilmoita poliisille ja riistanhoitoyhdistykselle,
    3. Hae korvaus Maaseutuvirastosta
  output: Poikkeamaraportti
  source: src:PESA
- flow_id: FLOW_PESA_04
  trigger: Säännöllinen heartbeat
  action: 'pesaturvallisuus: rutiiniarviointi'
  output: Status-raportti
  source: src:PESA
KNOWLEDGE_TABLES:
  threats:
  - threat: Karhu
    severity: KRIITTINEN
    damage: Pesät tuhoutuvat täysin
    protection: Sähköaita
    source: src:PETU2
  - threat: Mäyrä
    severity: KESKITASO
    damage: Kaivaa pesän alta
    protection: Sähköaita + verkko maahan
    source: src:PETU1
  - threat: Tikka
    severity: MATALA
    damage: Hakkaa pesän kylkeä talvella
    protection: Metalliverkko pesän ympärille
    source: src:PETU1
  - threat: Hiiret
    severity: MATALA
    damage: Talvipesässä vahakennon tuhoaminen
    protection: Lennonsäätäjä pienennä 6mm
    source: src:PETU1
COMPLIANCE_AND_LEGAL:
  damage_compensation: Petoeläinten aiheuttamista mehiläisvahingoista voi hakea korvausta
    Ruokavirastolta (vahingot ilmoitettava 7 pv sisällä) [src:PETU2]
  electric_fence: Sähköaitapaimenen tulee täyttää SFS-EN 60335-2-76 standardi [src:PETU1]
UNCERTAINTY_NOTES:
- Karhun käyttäytyminen on yksilöllistä — kokenut karhu voi oppia kiertämään aidan.
SOURCE_REGISTRY:
  sources:
  - id: src:PETU1
    org: SML / ProAgria
    title: Mehiläistarhan sähköaita
    year: 2024
    url: null
    supports: Sähköaitaaminen, jännite, maadoitus, ylläpito.
  - id: src:PETU2
    org: Luonnonvarakeskus (Luke)
    title: Petovahingot ja korvaukset
    year: 2025
    url: https://www.luke.fi/fi/tutkimus/suurpetotutkimus
    supports: Karhuvahingot, korvausmenettely.
eval_questions:
- q: Mikä on sähköaidan minimijännite?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.value
  source: src:PETU1
- q: Mikä on maadoituksen maksimiresistanssi?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fence_ground_resistance_ohm.value
  source: src:PETU1
- q: Milloin aita tarkistetaan useimmin?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action
  source: src:PETU1
- q: Mikä on karhuhavainnon hälytysraja (km)?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.bear_damage_radius_km.value
  source: src:PETU2
- q: Mikä on aidan korkeus?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fence_height_cm.value
  source: src:PETU1
- q: Miksi ruoho pitää leikata aidan alla?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.grass_under_fence_max_cm.action
  source: src:PETU1
- q: Mitä tehdään karhuvahingon jälkeen?
  a_ref: FAILURE_MODES[0].action
  source: src:PETU2
- q: Mistä karhuvahingon korvaus haetaan?
  a_ref: COMPLIANCE_AND_LEGAL.damage_compensation
  source: src:PETU2
- q: Mikä on vahingon ilmoitusaika?
  a_ref: COMPLIANCE_AND_LEGAL.damage_compensation
  source: src:PETU2
- q: Miten matala jännite korjataan?
  a_ref: FAILURE_MODES[1].action
  source: src:PETU1
- q: Mikä on karhun uhkataso pesille?
  a_ref: KNOWLEDGE_TABLES.threats[0].severity
  source: src:PETU2
- q: Miten mäyrältä suojaudutaan?
  a_ref: KNOWLEDGE_TABLES.threats[1].protection
  source: src:PETU1
- q: Miten tikka vahingoittaa pesää?
  a_ref: KNOWLEDGE_TABLES.threats[2].damage
  source: src:PETU1
- q: Miten hiiriltä suojaudutaan talvella?
  a_ref: KNOWLEDGE_TABLES.threats[3].protection
  source: src:PETU1
- q: Milloin aita pystytetään keväällä?
  a_ref: SEASONAL_RULES[0].action
  source: src:PETU1
- q: Kuinka usein ruoho niitetään?
  a_ref: SEASONAL_RULES[1].action
  source: src:PETU1
- q: Milloin aita voidaan poistaa?
  a_ref: SEASONAL_RULES[3].action
  source: src:PETU1
- q: Mikä standardi koskee sähköpaimenta?
  a_ref: COMPLIANCE_AND_LEGAL.electric_fence
  source: src:PETU1
- q: Mikä on alin langan korkeus?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fence_height_cm.note
  source: src:PETU1
- q: Voiko karhu kiertää aidan?
  a_ref: UNCERTAINTY_NOTES
  source: src:PETU2
- q: Kenelle karhuvahinko ilmoitetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:PETU2
- q: Mikä on ruohon maksimikorkeus aidan alla?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.grass_under_fence_max_cm.value
  source: src:PETU1
- q: Miten jännite mitataan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action
  source: src:PETU1
- q: Miten maadoitusta parannetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fence_ground_resistance_ohm.action
  source: src:PETU1
- q: Mikä on mäyrän vahinkotaso?
  a_ref: KNOWLEDGE_TABLES.threats[1].severity
  source: src:PETU1
- q: Mitä talvipesille tehdään syksyllä?
  a_ref: SEASONAL_RULES[2].action
  source: src:PETU1
- q: Milloin aita pidetään viimeksi päällä?
  a_ref: SEASONAL_RULES[2].action
  source: src:PETU1
- q: Mikä on lennonsäätäjän merkitys hiirien torjunnassa?
  a_ref: KNOWLEDGE_TABLES.threats[3].protection
  source: src:PETU1
- q: Miten karhujäljet tunnistetaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:PETU2
- q: Kuka tarkistaa aidan jännitteen?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action
  source: src:PETU1
- q: 'Operatiivinen päätöskysymys #1?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #2?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #3?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #4?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #5?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #6?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #7?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #8?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #9?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
- q: 'Operatiivinen päätöskysymys #10?'
  a_ref: DECISION_METRICS_AND_THRESHOLDS
  source: src:PESA
```

**sources.yaml:**
```yaml
sources:
- id: src:PETU1
  org: SML / ProAgria
  title: Mehiläistarhan sähköaita
  year: 2024
  url: null
  supports: Sähköaitaaminen, jännite, maadoitus, ylläpito.
- id: src:PETU2
  org: Luonnonvarakeskus (Luke)
  title: Petovahingot ja korvaukset
  year: 2025
  url: https://www.luke.fi/fi/tutkimus/suurpetotutkimus
  supports: Karhuvahingot, korvausmenettely.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Pesäturvallisuuspäällikkö (karhut ym.)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Karhu- ja mäyrävahinkojen ehkäisy mehiläistarhoilla
- Sähköaita ensisijainen suojakeino
- Korvenranta + kaikki muut tarha-sijainnit

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| electric_fence_voltage_kv | 4-7 kV (minimi 3 kV, alle → ei estä karhua) | Mittaa vähintään 2x/kk, ennen karhukautta (touko) viikoittain | src:PETU1 |
| fence_ground_resistance_ohm | <300 Ω | >300 Ω → paranna maadoitusta (lisää sauvoja, kastele) | src:PETU1 |
| bear_damage_radius_km | 5 | Karhuhavainto <5 km → nosta varotaso, tarkista aidat | src:PETU2 |
| fence_height_cm | 90-120 | Karhulle riittävä, alin lanka 20 cm maasta | src:PETU1 |
| grass_under_fence_max_cm | 10 | Ruoho >10 cm → niitä, vuotaa maahan | src:PETU1 |

## Tietotaulukot

**threats:**

| threat | severity | damage | protection | source |
| --- | --- | --- | --- | --- |
| Karhu | KRIITTINEN | Pesät tuhoutuvat täysin | Sähköaita | src:PETU2 |
| Mäyrä | KESKITASO | Kaivaa pesän alta | Sähköaita + verkko maahan | src:PETU1 |
| Tikka | MATALA | Hakkaa pesän kylkeä talvella | Metalliverkko pesän ympärille | src:PETU1 |
| Hiiret | MATALA | Talvipesässä vahakennon tuhoaminen | Lennonsäätäjä pienennä 6mm | src:PETU1 |

## Prosessit

**FLOW_PESA_01:** electric_fence_voltage_kv ylittää kynnysarvon (4-7 kV (minimi 3 kV, alle → ei estä karhua))
  → Mittaa vähintään 2x/kk, ennen karhukautta (touko) viikoittain
  Tulos: Tilanneraportti

**FLOW_PESA_02:** Kausi vaihtuu: Kevät (huhti-touko)
  → [vko 14-22] Sähköaidan pystytys/tarkistus ENNEN karhun heräämistä. Jännitemittaus. Maadoituksen test
  Tulos: Tarkistuslista

**FLOW_PESA_03:** Havaittu: Karhu murtautunut aitaan
  → 1. Dokumentoi (valokuva + päiväys), 2. Ilmoita poliisille ja riistanhoitoyhdistykselle, 3. Hae korvaus Maaseutuvirastosta
  Tulos: Poikkeamaraportti

**FLOW_PESA_04:** Säännöllinen heartbeat
  → pesaturvallisuus: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät (huhti-touko)** | [vko 14-22] Sähköaidan pystytys/tarkistus ENNEN karhun heräämistä. Jännitemittaus. Maadoituksen testaus. | src:PETU1 |
| **Kesä** | Ruohon niitto aidan alla 2x/kk. Jännite 2x/kk. Riistanvartijalta karhu-info seurantaan. | src:PETU1 |
| **Syksy** | [vko 36-48] Aita pidetään päällä lokakuun loppuun. Pesäkohtaiset suojaukset talvipesille (hiiri, tikka). | src:PETU1 |
| **Talvi** | [vko 49-13] Aita voidaan poistaa lumitöiden ajaksi (karhu talviunessa). Hiiriverkon tarkistus. | src:PETU1 |

## Virhe- ja vaaratilanteet

### ⚠️ Karhu murtautunut aitaan
- **Havaitseminen:** Kaatuneet pylväät, tuhoutuneet pesät, karhun jäljet
- **Toimenpide:** 1. Dokumentoi (valokuva + päiväys), 2. Ilmoita poliisille ja riistanhoitoyhdistykselle, 3. Hae korvaus Maaseutuvirastosta
- **Lähde:** src:PETU2

### ⚠️ Aidan jännite liian matala
- **Havaitseminen:** Mittari <3 kV
- **Toimenpide:** Tarkista: ruoho, maadoitus, akku/verkkosyöttö, johdon eristeet
- **Lähde:** src:PETU1

## Lait ja vaatimukset
- **damage_compensation:** Petoeläinten aiheuttamista mehiläisvahingoista voi hakea korvausta Ruokavirastolta (vahingot ilmoitettava 7 pv sisällä) [src:PETU2]
- **electric_fence:** Sähköaitapaimenen tulee täyttää SFS-EN 60335-2-76 standardi [src:PETU1]

## Epävarmuudet
- Karhun käyttäytyminen on yksilöllistä — kokenut karhu voi oppia kiertämään aidan.

## Lähteet
- **src:PETU1**: SML / ProAgria — *Mehiläistarhan sähköaita* (2024) —
- **src:PETU2**: Luonnonvarakeskus (Luke) — *Petovahingot ja korvaukset* (2025) https://www.luke.fi/fi/tutkimus/suurpetotutkimus

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on sähköaidan minimijännite?**
    → `DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.value` [src:PETU1]
 2. **Mikä on maadoituksen maksimiresistanssi?**
    → `DECISION_METRICS_AND_THRESHOLDS.fence_ground_resistance_ohm.value` [src:PETU1]
 3. **Milloin aita tarkistetaan useimmin?**
    → `DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action` [src:PETU1]
 4. **Mikä on karhuhavainnon hälytysraja (km)?**
    → `DECISION_METRICS_AND_THRESHOLDS.bear_damage_radius_km.value` [src:PETU2]
 5. **Mikä on aidan korkeus?**
    → `DECISION_METRICS_AND_THRESHOLDS.fence_height_cm.value` [src:PETU1]
 6. **Miksi ruoho pitää leikata aidan alla?**
    → `DECISION_METRICS_AND_THRESHOLDS.grass_under_fence_max_cm.action` [src:PETU1]
 7. **Mitä tehdään karhuvahingon jälkeen?**
    → `FAILURE_MODES[0].action` [src:PETU2]
 8. **Mistä karhuvahingon korvaus haetaan?**
    → `COMPLIANCE_AND_LEGAL.damage_compensation` [src:PETU2]
 9. **Mikä on vahingon ilmoitusaika?**
    → `COMPLIANCE_AND_LEGAL.damage_compensation` [src:PETU2]
10. **Miten matala jännite korjataan?**
    → `FAILURE_MODES[1].action` [src:PETU1]
11. **Mikä on karhun uhkataso pesille?**
    → `KNOWLEDGE_TABLES.threats[0].severity` [src:PETU2]
12. **Miten mäyrältä suojaudutaan?**
    → `KNOWLEDGE_TABLES.threats[1].protection` [src:PETU1]
13. **Miten tikka vahingoittaa pesää?**
    → `KNOWLEDGE_TABLES.threats[2].damage` [src:PETU1]
14. **Miten hiiriltä suojaudutaan talvella?**
    → `KNOWLEDGE_TABLES.threats[3].protection` [src:PETU1]
15. **Milloin aita pystytetään keväällä?**
    → `SEASONAL_RULES[0].action` [src:PETU1]
16. **Kuinka usein ruoho niitetään?**
    → `SEASONAL_RULES[1].action` [src:PETU1]
17. **Milloin aita voidaan poistaa?**
    → `SEASONAL_RULES[3].action` [src:PETU1]
18. **Mikä standardi koskee sähköpaimenta?**
    → `COMPLIANCE_AND_LEGAL.electric_fence` [src:PETU1]
19. **Mikä on alin langan korkeus?**
    → `DECISION_METRICS_AND_THRESHOLDS.fence_height_cm.note` [src:PETU1]
20. **Voiko karhu kiertää aidan?**
    → `UNCERTAINTY_NOTES` [src:PETU2]
21. **Kenelle karhuvahinko ilmoitetaan?**
    → `FAILURE_MODES[0].action` [src:PETU2]
22. **Mikä on ruohon maksimikorkeus aidan alla?**
    → `DECISION_METRICS_AND_THRESHOLDS.grass_under_fence_max_cm.value` [src:PETU1]
23. **Miten jännite mitataan?**
    → `DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action` [src:PETU1]
24. **Miten maadoitusta parannetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.fence_ground_resistance_ohm.action` [src:PETU1]
25. **Mikä on mäyrän vahinkotaso?**
    → `KNOWLEDGE_TABLES.threats[1].severity` [src:PETU1]
26. **Mitä talvipesille tehdään syksyllä?**
    → `SEASONAL_RULES[2].action` [src:PETU1]
27. **Milloin aita pidetään viimeksi päällä?**
    → `SEASONAL_RULES[2].action` [src:PETU1]
28. **Mikä on lennonsäätäjän merkitys hiirien torjunnassa?**
    → `KNOWLEDGE_TABLES.threats[3].protection` [src:PETU1]
29. **Miten karhujäljet tunnistetaan?**
    → `FAILURE_MODES[0].detection` [src:PETU2]
30. **Kuka tarkistaa aidan jännitteen?**
    → `DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.action` [src:PETU1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 19
## AGENT 19: Limnologi (Järvitutkija)
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: limnologi
  agent_name: Limnologi (Järvitutkija)
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Huhdasjärvi, Kouvola — pieni metsäjärvi
- 'Käyttö: uinti, kalastus, veneily, veden laatu'
DECISION_METRICS_AND_THRESHOLDS:
  water_temp_swimming_min_c:
    value: 15
    action: <15°C → hypotermia-varoitus
    source: src:LIM1
  secchi_depth_m:
    value: Normaali 2-4 m
    action: <1.5 m → leväkukinta-epäily
    source: src:LIM1
  cyanobacteria_visual:
    value: Vihreä maalivana pinnalla
    action: UINTIKIELTO, ilmoita rantavahdille, näyte SYKE:lle
    source: src:LIM2
  ph_range:
    value: Normaali humusjärvi 5.5-7.0
    action: <5.0 → happamoituminen, >8.5 → leväkukinta
    source: src:LIM1
  oxygen_mg_per_l:
    value: '>6 mg/l normaali'
    action: <4 mg/l → kalakuolemaviski, <2 mg/l → anoxia
    source: src:LIM1
  phosphorus_ug_per_l:
    value: <15 karu, 15-25 lievästi rehevä, >50 rehevä
    source: src:LIM1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Jäiden lähdön jälkeen kevättäyskierto. Vesinäyte touko-kesäkuussa.'
  source: src:LIM1
- season: Kesä
  action: '[vko 22-35] Leväkukintaseuranta viikoittain. Termokliini. Sinilevävaroitukset.'
  source: src:LIM2
- season: Syksy
  action: '[vko 36-48] Syystäyskierto. Vesinäyte syyskuussa.'
  source: src:LIM1
- season: Talvi
  action: '[vko 49-13] Jään alla happi kuluu. Talvinäyte helmikuussa. Lumi estää valoa
    → happi laskee.'
  source: src:LIM1
FAILURE_MODES:
- mode: Sinilevähavainto
  detection: Vihreä maalivana, haju
  action: UINTIKIELTO, näyte SYKE:lle, ilmoita kunnan ympäristöviranomaiselle
  source: src:LIM2
- mode: Kalakuolema
  detection: Kuolleita kaloja pinnalla/rannalla
  action: Ilmoita ELY-keskukselle, vesinäyte (happi+lämpö), dokumentoi
  source: src:LIM1
PROCESS_FLOWS:
- flow_id: FLOW_LIMN_01
  trigger: water_temp_swimming_min_c ylittää kynnysarvon (15)
  action: <15°C → hypotermia-varoitus
  output: Tilanneraportti
  source: src:LIMN
- flow_id: FLOW_LIMN_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Jäiden lähdön jälkeen kevättäyskierto. Vesinäyte touko-kesäkuussa.'
  output: Tarkistuslista
  source: src:LIMN
- flow_id: FLOW_LIMN_03
  trigger: 'Havaittu: Sinilevähavainto'
  action: UINTIKIELTO, näyte SYKE:lle, ilmoita kunnan ympäristöviranomaiselle
  output: Poikkeamaraportti
  source: src:LIMN
- flow_id: FLOW_LIMN_04
  trigger: Säännöllinen heartbeat
  action: 'limnologi: rutiiniarviointi'
  output: Status-raportti
  source: src:LIMN
KNOWLEDGE_TABLES:
  lake_status:
  - indicator: Secchi
    good: '>3 m'
    poor: <1.5 m
  - indicator: Kok.fosfori
    good: <15 μg/l
    poor: '>40 μg/l'
  - indicator: Happi (pohja)
    good: '>6 mg/l'
    poor: <2 mg/l
  - indicator: Klorofylli-a
    good: <4 μg/l
    poor: '>20 μg/l'
COMPLIANCE_AND_LEGAL:
  water_quality: 'Ympäristönsuojelulaki 527/2014: pilaamiskielto [src:LIM3]'
  bathing_water: Sinilevä → uintikielto uimavesidirektiivin mukaisesti [src:LIM2]
UNCERTAINTY_NOTES:
- Pienen metsäjärven tila voi vaihdella nopeasti sään mukaan.
SOURCE_REGISTRY:
  sources:
  - id: src:LIM1
    org: SYKE
    title: Pintavesien tila
    year: 2024
    url: https://www.syke.fi/
    supports: Vedenlaatu, indikaattorit.
  - id: src:LIM2
    org: THL/SYKE
    title: Sinileväopas
    year: 2025
    url: https://www.jarviwiki.fi/
    supports: Sinilevätunnistus.
  - id: src:LIM3
    org: Oikeusministeriö
    title: Ympäristönsuojelulaki 527/2014
    year: 2014
    url: https://finlex.fi/fi/laki/ajantasa/2014/20140527
    supports: Pilaamiskielto.
eval_questions:
- q: Mikä on mukava uintilämpötila?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.water_temp_swimming_min_c
  source: src:LIM1
- q: Miten sinileväkukinta tunnistetaan?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cyanobacteria_visual.value
  source: src:LIM2
- q: Kenelle kalakuolema ilmoitetaan?
  a_ref: FAILURE_MODES[1].action
  source: src:LIM1
- q: Mikä on karun järven fosforiraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.phosphorus_ug_per_l.value
  source: src:LIM1
- q: Mikä on anoxian happiraja?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.oxygen_mg_per_l.action
  source: src:LIM1
- q: Kenelle sinilevävaroitus annetaan?
  a_ref: FAILURE_MODES[0].action
  source: src:LIM2
- q: Milloin talvinäyte otetaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:LIM1
- q: Mikä on kevättäyskierron merkitys?
  a_ref: SEASONAL_RULES[0].action
  source: src:LIM1
- q: Miten lumi vaikuttaa talvella happeen?
  a_ref: SEASONAL_RULES[3].action
  source: src:LIM1
- q: Mikä on Secchi-syvyyden hyvä arvo?
  a_ref: KNOWLEDGE_TABLES.lake_status[0].good
  source: src:LIM1
- q: Mikä on water temp swimming min c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.water_temp_swimming_min_c
  source: src:LIM1
- q: Mitä tehdään kun water temp swimming min c ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.water_temp_swimming_min_c.action
  source: src:LIM1
- q: Mikä on secchi depth m?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.secchi_depth_m
  source: src:LIM1
- q: Mitä tehdään kun secchi depth m ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.secchi_depth_m.action
  source: src:LIM1
- q: Mikä on cyanobacteria visual?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cyanobacteria_visual
  source: src:LIM1
- q: Mitä tehdään kun cyanobacteria visual ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.cyanobacteria_visual.action
  source: src:LIM1
- q: Mikä on ph range?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ph_range
  source: src:LIM1
- q: Mitä tehdään kun ph range ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ph_range.action
  source: src:LIM1
- q: Mikä on oxygen mg per l?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.oxygen_mg_per_l
  source: src:LIM1
- q: Mitä tehdään kun oxygen mg per l ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.oxygen_mg_per_l.action
  source: src:LIM1
- q: Mikä on phosphorus ug per l?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.phosphorus_ug_per_l
  source: src:LIM1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:LIM1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:LIM1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:LIM1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:LIM1
- q: Miten 'Sinilevähavainto' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:LIM1
- q: Mitä tehdään tilanteessa 'Sinilevähavainto'?
  a_ref: FAILURE_MODES[0].action
  source: src:LIM1
- q: Miten 'Kalakuolema' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:LIM1
- q: Mitä tehdään tilanteessa 'Kalakuolema'?
  a_ref: FAILURE_MODES[1].action
  source: src:LIM1
- q: Mikä on water quality -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.water_quality
  source: src:LIM1
- q: Mikä on bathing water -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.bathing_water
  source: src:LIM1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:LIM1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:LIM1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:LIM1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:LIM1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:LIM1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:LIM1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:LIM1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:LIM1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:LIM1
```

**sources.yaml:**
```yaml
sources:
- id: src:LIM1
  org: SYKE
  title: Pintavesien tila
  year: 2024
  url: https://www.syke.fi/
  supports: Vedenlaatu, indikaattorit.
- id: src:LIM2
  org: THL/SYKE
  title: Sinileväopas
  year: 2025
  url: https://www.jarviwiki.fi/
  supports: Sinilevätunnistus.
- id: src:LIM3
  org: Oikeusministeriö
  title: Ympäristönsuojelulaki 527/2014
  year: 2014
  url: https://finlex.fi/fi/laki/ajantasa/2014/20140527
  supports: Pilaamiskielto.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Limnologi (Järvitutkija)
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Huhdasjärvi, Kouvola — pieni metsäjärvi
- Käyttö: uinti, kalastus, veneily, veden laatu

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| water_temp_swimming_min_c | 15 | <15°C → hypotermia-varoitus | src:LIM1 |
| secchi_depth_m | Normaali 2-4 m | <1.5 m → leväkukinta-epäily | src:LIM1 |
| cyanobacteria_visual | Vihreä maalivana pinnalla | UINTIKIELTO, ilmoita rantavahdille, näyte SYKE:lle | src:LIM2 |
| ph_range | Normaali humusjärvi 5.5-7.0 | <5.0 → happamoituminen, >8.5 → leväkukinta | src:LIM1 |
| oxygen_mg_per_l | >6 mg/l normaali | <4 mg/l → kalakuolemaviski, <2 mg/l → anoxia | src:LIM1 |
| phosphorus_ug_per_l | <15 karu, 15-25 lievästi rehevä, >50 rehevä | — | src:LIM1 |

## Tietotaulukot

**lake_status:**

| indicator | good | poor |
| --- | --- | --- |
| Secchi | >3 m | <1.5 m |
| Kok.fosfori | <15 μg/l | >40 μg/l |
| Happi (pohja) | >6 mg/l | <2 mg/l |
| Klorofylli-a | <4 μg/l | >20 μg/l |

## Prosessit

**FLOW_LIMN_01:** water_temp_swimming_min_c ylittää kynnysarvon (15)
  → <15°C → hypotermia-varoitus
  Tulos: Tilanneraportti

**FLOW_LIMN_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Jäiden lähdön jälkeen kevättäyskierto. Vesinäyte touko-kesäkuussa.
  Tulos: Tarkistuslista

**FLOW_LIMN_03:** Havaittu: Sinilevähavainto
  → UINTIKIELTO, näyte SYKE:lle, ilmoita kunnan ympäristöviranomaiselle
  Tulos: Poikkeamaraportti

**FLOW_LIMN_04:** Säännöllinen heartbeat
  → limnologi: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Jäiden lähdön jälkeen kevättäyskierto. Vesinäyte touko-kesäkuussa. | src:LIM1 |
| **Kesä** | [vko 22-35] Leväkukintaseuranta viikoittain. Termokliini. Sinilevävaroitukset. | src:LIM2 |
| **Syksy** | [vko 36-48] Syystäyskierto. Vesinäyte syyskuussa. | src:LIM1 |
| **Talvi** | [vko 49-13] Jään alla happi kuluu. Talvinäyte helmikuussa. Lumi estää valoa → happi laskee. | src:LIM1 |

## Virhe- ja vaaratilanteet

### ⚠️ Sinilevähavainto
- **Havaitseminen:** Vihreä maalivana, haju
- **Toimenpide:** UINTIKIELTO, näyte SYKE:lle, ilmoita kunnan ympäristöviranomaiselle
- **Lähde:** src:LIM2

### ⚠️ Kalakuolema
- **Havaitseminen:** Kuolleita kaloja pinnalla/rannalla
- **Toimenpide:** Ilmoita ELY-keskukselle, vesinäyte (happi+lämpö), dokumentoi
- **Lähde:** src:LIM1

## Lait ja vaatimukset
- **water_quality:** Ympäristönsuojelulaki 527/2014: pilaamiskielto [src:LIM3]
- **bathing_water:** Sinilevä → uintikielto uimavesidirektiivin mukaisesti [src:LIM2]

## Epävarmuudet
- Pienen metsäjärven tila voi vaihdella nopeasti sään mukaan.

## Lähteet
- **src:LIM1**: SYKE — *Pintavesien tila* (2024) https://www.syke.fi/
- **src:LIM2**: THL/SYKE — *Sinileväopas* (2025) https://www.jarviwiki.fi/
- **src:LIM3**: Oikeusministeriö — *Ympäristönsuojelulaki 527/2014* (2014) https://finlex.fi/fi/laki/ajantasa/2014/20140527

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on mukava uintilämpötila?**
    → `DECISION_METRICS_AND_THRESHOLDS.water_temp_swimming_min_c` [src:LIM1]
 2. **Miten sinileväkukinta tunnistetaan?**
    → `DECISION_METRICS_AND_THRESHOLDS.cyanobacteria_visual.value` [src:LIM2]
 3. **Kenelle kalakuolema ilmoitetaan?**
    → `FAILURE_MODES[1].action` [src:LIM1]
 4. **Mikä on karun järven fosforiraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.phosphorus_ug_per_l.value` [src:LIM1]
 5. **Mikä on anoxian happiraja?**
    → `DECISION_METRICS_AND_THRESHOLDS.oxygen_mg_per_l.action` [src:LIM1]
 6. **Kenelle sinilevävaroitus annetaan?**
    → `FAILURE_MODES[0].action` [src:LIM2]
 7. **Milloin talvinäyte otetaan?**
    → `SEASONAL_RULES[3].action` [src:LIM1]
 8. **Mikä on kevättäyskierron merkitys?**
    → `SEASONAL_RULES[0].action` [src:LIM1]
 9. **Miten lumi vaikuttaa talvella happeen?**
    → `SEASONAL_RULES[3].action` [src:LIM1]
10. **Mikä on Secchi-syvyyden hyvä arvo?**
    → `KNOWLEDGE_TABLES.lake_status[0].good` [src:LIM1]
11. **Mikä on water temp swimming min c?**
    → `DECISION_METRICS_AND_THRESHOLDS.water_temp_swimming_min_c` [src:LIM1]
12. **Mitä tehdään kun water temp swimming min c ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.water_temp_swimming_min_c.action` [src:LIM1]
13. **Mikä on secchi depth m?**
    → `DECISION_METRICS_AND_THRESHOLDS.secchi_depth_m` [src:LIM1]
14. **Mitä tehdään kun secchi depth m ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.secchi_depth_m.action` [src:LIM1]
15. **Mikä on cyanobacteria visual?**
    → `DECISION_METRICS_AND_THRESHOLDS.cyanobacteria_visual` [src:LIM1]
16. **Mitä tehdään kun cyanobacteria visual ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.cyanobacteria_visual.action` [src:LIM1]
17. **Mikä on ph range?**
    → `DECISION_METRICS_AND_THRESHOLDS.ph_range` [src:LIM1]
18. **Mitä tehdään kun ph range ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.ph_range.action` [src:LIM1]
19. **Mikä on oxygen mg per l?**
    → `DECISION_METRICS_AND_THRESHOLDS.oxygen_mg_per_l` [src:LIM1]
20. **Mitä tehdään kun oxygen mg per l ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.oxygen_mg_per_l.action` [src:LIM1]
21. **Mikä on phosphorus ug per l?**
    → `DECISION_METRICS_AND_THRESHOLDS.phosphorus_ug_per_l` [src:LIM1]
22. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:LIM1]
23. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:LIM1]
24. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:LIM1]
25. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:LIM1]
26. **Miten 'Sinilevähavainto' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:LIM1]
27. **Mitä tehdään tilanteessa 'Sinilevähavainto'?**
    → `FAILURE_MODES[0].action` [src:LIM1]
28. **Miten 'Kalakuolema' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:LIM1]
29. **Mitä tehdään tilanteessa 'Kalakuolema'?**
    → `FAILURE_MODES[1].action` [src:LIM1]
30. **Mikä on water quality -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.water_quality` [src:LIM1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 20
## AGENT 20: Kalastusopas
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: kalastusopas
  agent_name: Kalastusopas
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Huhdasjärvi + lähivesistöt
- Onkiminen, pilkkiminen, heittokalastus
DECISION_METRICS_AND_THRESHOLDS:
  pike_active_temp_c:
    value: 8-18°C
    source: src:KAL1
    action: 8-18°C → aktiivisin. >20°C → siirtyvät syvemmälle, vaihda painotettu viehe.
      <5°C → hauki passiivinen, hidas esitys.
  perch_spawn_temp_c:
    value: 8-12°C (huhti-touko)
    source: src:KAL1
    action: 8-12°C (vko 18-21 Kouvolassa) → RAUHOITA kutualueet. Vältä rantakalastusta
      kutuaikaan.
  fishing_license:
    value: Kalastonhoitomaksu 18-64v, 45€/v (2026)
    source: src:KAL2
  pike_min_size_cm:
    value: 40
    source: src:KAL2
  zander_min_size_cm:
    value: 42
    source: src:KAL2
  barometric_optimal_hpa:
    value: 1010-1020, laskeva → aktiivinen syönti
    action: 1010-1020 hPa laskeva → paras syönti. >1025 nouseva → heikko syönti. Muutos
      >10 hPa/12h → kalat aktiivisia.
    source: src:KAL1
SEASONAL_RULES:
- season: Kevät
  action: Hauki rauhoitettu 1.4.-31.5. Jäiden lähtö → ensimmäiset kalastusmahdollisuudet.
  source: src:KAL2
- season: Kesä
  action: Ahven/kuha aktiivisia. Veden lämmetessä >20°C kalat syvemmällä.
  source: src:KAL1
- season: Syksy
  action: '[vko 36-48] Paras hauenkalastuskausi. Kuha aktiivinen hämärässä.'
  source: src:KAL1
- season: Talvi
  action: Pilkkiminen jään tultua (>10 cm). Ahven parasta.
  source: src:KAL1
FAILURE_MODES:
- mode: Kalastus rauhoitusaikana
  detection: Käyttäjä ei tiedä rauhoitusta
  action: Tarkista Kalastusrajoitus.fi ennen kalastusta
  source: src:KAL2
- mode: Alamittoinen kala
  detection: Kala alle alamitan
  action: Vapauta VEDESSÄ, älä nosta veneeseen
  source: src:KAL2
PROCESS_FLOWS:
- flow_id: FLOW_KALA_01
  trigger: pike_active_temp_c ylittää kynnysarvon (8-18°C)
  action: 8-18°C → aktiivisin. >20°C → siirtyvät syvemmälle, vaihda painotettu viehe.
    <5°C → hauki passiivinen, hidas esitys.
  output: Tilanneraportti
  source: src:KALA
- flow_id: FLOW_KALA_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: Hauki rauhoitettu 1.4.-31.5. Jäiden lähtö → ensimmäiset kalastusmahdollisuudet.
  output: Tarkistuslista
  source: src:KALA
- flow_id: FLOW_KALA_03
  trigger: 'Havaittu: Kalastus rauhoitusaikana'
  action: Tarkista Kalastusrajoitus.fi ennen kalastusta
  output: Poikkeamaraportti
  source: src:KALA
- flow_id: FLOW_KALA_04
  trigger: Säännöllinen heartbeat
  action: 'kalastusopas: rutiiniarviointi'
  output: Status-raportti
  source: src:KALA
KNOWLEDGE_TABLES:
  species:
  - laji: Hauki
    rauhoitus: 1.4.-31.5.
    alamitta_cm: 40
    paras_aika: Touko-kesä, syys-loka
  - laji: Ahven
    rauhoitus: Ei
    alamitta_cm: Ei
    paras_aika: Kesä, talvi (pilkki)
  - laji: Kuha
    rauhoitus: 15.4.-15.6. (aluekohtainen)
    alamitta_cm: 42
    paras_aika: Kesäillat, syksy
  - laji: Lahna
    rauhoitus: Ei
    alamitta_cm: Ei
    paras_aika: Kesäkuu, loppukesä
COMPLIANCE_AND_LEGAL:
  kalastonhoitomaksu: Kalastuslaki 379/2015 [src:KAL2]
  rauhoitukset: Hauki 1.4.-31.5., Kuha 15.4.-15.6. (aluekohtainen) [src:KAL2]
  vesialueen_lupa: Viehekalastus vaatii vesialueen luvat [src:KAL2]
UNCERTAINTY_NOTES:
- Alamitat ja rauhoitukset voivat muuttua — tarkista Kalastusrajoitus.fi.
SOURCE_REGISTRY:
  sources:
  - id: src:KAL1
    org: Luke
    title: Kalalajien ekologia
    year: 2024
    url: https://www.luke.fi/
    supports: Kalojen käyttäytyminen.
  - id: src:KAL2
    org: MMM
    title: Kalastuslaki 379/2015
    year: 2015
    url: https://kalastusrajoitus.fi/
    supports: Luvat, rauhoitukset, alamitat.
eval_questions:
- q: Milloin hauki on rauhoitettu?
  a_ref: KNOWLEDGE_TABLES.species[0].rauhoitus
  source: src:KAL2
- q: Mikä on hauen alamitta?
  a_ref: KNOWLEDGE_TABLES.species[0].alamitta_cm
  source: src:KAL2
- q: Mikä on kuhan alamitta?
  a_ref: KNOWLEDGE_TABLES.species[2].alamitta_cm
  source: src:KAL2
- q: Tarvitaanko kalastonhoitomaksu?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fishing_license
  source: src:KAL2
- q: Onko ahvenella alamittaa?
  a_ref: KNOWLEDGE_TABLES.species[1].alamitta_cm
  source: src:KAL2
- q: Milloin kuha on aktiivisin?
  a_ref: KNOWLEDGE_TABLES.species[2].paras_aika
  source: src:KAL2
- q: Miten alamittoinen kala käsitellään?
  a_ref: FAILURE_MODES[1].action
  source: src:KAL2
- q: Mikä sää on paras kalastukseen?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.barometric_optimal_hpa
  source: src:KAL1
- q: Mikä on jään minimipaksuus pilkinnälle?
  a_ref: SEASONAL_RULES[3].action
  source: src:KAL1
- q: Mikä laki säätelee kalastusta?
  a_ref: COMPLIANCE_AND_LEGAL.kalastonhoitomaksu
  source: src:KAL2
- q: Mikä on pike active temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pike_active_temp_c
  source: src:KAL1
- q: Mikä on perch spawn temp c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.perch_spawn_temp_c
  source: src:KAL1
- q: Mikä on fishing license?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.fishing_license
  source: src:KAL1
- q: Mikä on pike min size cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pike_min_size_cm
  source: src:KAL1
- q: Mikä on zander min size cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.zander_min_size_cm
  source: src:KAL1
- q: Mikä on barometric optimal hpa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.barometric_optimal_hpa
  source: src:KAL1
- q: Mitä tehdään kun barometric optimal hpa ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.barometric_optimal_hpa.action
  source: src:KAL1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:KAL1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:KAL1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:KAL1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:KAL1
- q: Miten 'Kalastus rauhoitusaikana' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:KAL1
- q: Mitä tehdään tilanteessa 'Kalastus rauhoitusaikana'?
  a_ref: FAILURE_MODES[0].action
  source: src:KAL1
- q: Miten 'Alamittoinen kala' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:KAL1
- q: Mitä tehdään tilanteessa 'Alamittoinen kala'?
  a_ref: FAILURE_MODES[1].action
  source: src:KAL1
- q: Mikä on kalastonhoitomaksu -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.kalastonhoitomaksu
  source: src:KAL1
- q: Mikä on rauhoitukset -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.rauhoitukset
  source: src:KAL1
- q: Mikä on vesialueen lupa -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.vesialueen_lupa
  source: src:KAL1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:KAL1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:KAL1
```

**sources.yaml:**
```yaml
sources:
- id: src:KAL1
  org: Luke
  title: Kalalajien ekologia
  year: 2024
  url: https://www.luke.fi/
  supports: Kalojen käyttäytyminen.
- id: src:KAL2
  org: MMM
  title: Kalastuslaki 379/2015
  year: 2015
  url: https://kalastusrajoitus.fi/
  supports: Luvat, rauhoitukset, alamitat.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Kalastusopas
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Huhdasjärvi + lähivesistöt
- Onkiminen, pilkkiminen, heittokalastus

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| pike_active_temp_c | 8-18°C | 8-18°C → aktiivisin. >20°C → siirtyvät syvemmälle, vaihda painotettu viehe. <5°C → hauki passiivinen, hidas esitys. | src:KAL1 |
| perch_spawn_temp_c | 8-12°C (huhti-touko) | 8-12°C (vko 18-21 Kouvolassa) → RAUHOITA kutualueet. Vältä rantakalastusta kutuaikaan. | src:KAL1 |
| fishing_license | Kalastonhoitomaksu 18-64v, 45€/v (2026) | — | src:KAL2 |
| pike_min_size_cm | 40 | — | src:KAL2 |
| zander_min_size_cm | 42 | — | src:KAL2 |
| barometric_optimal_hpa | 1010-1020, laskeva → aktiivinen syönti | 1010-1020 hPa laskeva → paras syönti. >1025 nouseva → heikko syönti. Muutos >10 hPa/12h → kalat aktiivisia. | src:KAL1 |

## Tietotaulukot

**species:**

| laji | rauhoitus | alamitta_cm | paras_aika |
| --- | --- | --- | --- |
| Hauki | 1.4.-31.5. | 40 | Touko-kesä, syys-loka |
| Ahven | Ei | Ei | Kesä, talvi (pilkki) |
| Kuha | 15.4.-15.6. (aluekohtainen) | 42 | Kesäillat, syksy |
| Lahna | Ei | Ei | Kesäkuu, loppukesä |

## Prosessit

**FLOW_KALA_01:** pike_active_temp_c ylittää kynnysarvon (8-18°C)
  → 8-18°C → aktiivisin. >20°C → siirtyvät syvemmälle, vaihda painotettu viehe. <5°C → hauki passiivinen, hidas esitys.
  Tulos: Tilanneraportti

**FLOW_KALA_02:** Kausi vaihtuu: Kevät
  → Hauki rauhoitettu 1.4.-31.5. Jäiden lähtö → ensimmäiset kalastusmahdollisuudet.
  Tulos: Tarkistuslista

**FLOW_KALA_03:** Havaittu: Kalastus rauhoitusaikana
  → Tarkista Kalastusrajoitus.fi ennen kalastusta
  Tulos: Poikkeamaraportti

**FLOW_KALA_04:** Säännöllinen heartbeat
  → kalastusopas: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | Hauki rauhoitettu 1.4.-31.5. Jäiden lähtö → ensimmäiset kalastusmahdollisuudet. | src:KAL2 |
| **Kesä** | Ahven/kuha aktiivisia. Veden lämmetessä >20°C kalat syvemmällä. | src:KAL1 |
| **Syksy** | [vko 36-48] Paras hauenkalastuskausi. Kuha aktiivinen hämärässä. | src:KAL1 |
| **Talvi** | Pilkkiminen jään tultua (>10 cm). Ahven parasta. | src:KAL1 |

## Virhe- ja vaaratilanteet

### ⚠️ Kalastus rauhoitusaikana
- **Havaitseminen:** Käyttäjä ei tiedä rauhoitusta
- **Toimenpide:** Tarkista Kalastusrajoitus.fi ennen kalastusta
- **Lähde:** src:KAL2

### ⚠️ Alamittoinen kala
- **Havaitseminen:** Kala alle alamitan
- **Toimenpide:** Vapauta VEDESSÄ, älä nosta veneeseen
- **Lähde:** src:KAL2

## Lait ja vaatimukset
- **kalastonhoitomaksu:** Kalastuslaki 379/2015 [src:KAL2]
- **rauhoitukset:** Hauki 1.4.-31.5., Kuha 15.4.-15.6. (aluekohtainen) [src:KAL2]
- **vesialueen_lupa:** Viehekalastus vaatii vesialueen luvat [src:KAL2]

## Epävarmuudet
- Alamitat ja rauhoitukset voivat muuttua — tarkista Kalastusrajoitus.fi.

## Lähteet
- **src:KAL1**: Luke — *Kalalajien ekologia* (2024) https://www.luke.fi/
- **src:KAL2**: MMM — *Kalastuslaki 379/2015* (2015) https://kalastusrajoitus.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Milloin hauki on rauhoitettu?**
    → `KNOWLEDGE_TABLES.species[0].rauhoitus` [src:KAL2]
 2. **Mikä on hauen alamitta?**
    → `KNOWLEDGE_TABLES.species[0].alamitta_cm` [src:KAL2]
 3. **Mikä on kuhan alamitta?**
    → `KNOWLEDGE_TABLES.species[2].alamitta_cm` [src:KAL2]
 4. **Tarvitaanko kalastonhoitomaksu?**
    → `DECISION_METRICS_AND_THRESHOLDS.fishing_license` [src:KAL2]
 5. **Onko ahvenella alamittaa?**
    → `KNOWLEDGE_TABLES.species[1].alamitta_cm` [src:KAL2]
 6. **Milloin kuha on aktiivisin?**
    → `KNOWLEDGE_TABLES.species[2].paras_aika` [src:KAL2]
 7. **Miten alamittoinen kala käsitellään?**
    → `FAILURE_MODES[1].action` [src:KAL2]
 8. **Mikä sää on paras kalastukseen?**
    → `DECISION_METRICS_AND_THRESHOLDS.barometric_optimal_hpa` [src:KAL1]
 9. **Mikä on jään minimipaksuus pilkinnälle?**
    → `SEASONAL_RULES[3].action` [src:KAL1]
10. **Mikä laki säätelee kalastusta?**
    → `COMPLIANCE_AND_LEGAL.kalastonhoitomaksu` [src:KAL2]
11. **Mikä on pike active temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.pike_active_temp_c` [src:KAL1]
12. **Mikä on perch spawn temp c?**
    → `DECISION_METRICS_AND_THRESHOLDS.perch_spawn_temp_c` [src:KAL1]
13. **Mikä on fishing license?**
    → `DECISION_METRICS_AND_THRESHOLDS.fishing_license` [src:KAL1]
14. **Mikä on pike min size cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.pike_min_size_cm` [src:KAL1]
15. **Mikä on zander min size cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.zander_min_size_cm` [src:KAL1]
16. **Mikä on barometric optimal hpa?**
    → `DECISION_METRICS_AND_THRESHOLDS.barometric_optimal_hpa` [src:KAL1]
17. **Mitä tehdään kun barometric optimal hpa ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.barometric_optimal_hpa.action` [src:KAL1]
18. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:KAL1]
19. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:KAL1]
20. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:KAL1]
21. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:KAL1]
22. **Miten 'Kalastus rauhoitusaikana' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:KAL1]
23. **Mitä tehdään tilanteessa 'Kalastus rauhoitusaikana'?**
    → `FAILURE_MODES[0].action` [src:KAL1]
24. **Miten 'Alamittoinen kala' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:KAL1]
25. **Mitä tehdään tilanteessa 'Alamittoinen kala'?**
    → `FAILURE_MODES[1].action` [src:KAL1]
26. **Mikä on kalastonhoitomaksu -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.kalastonhoitomaksu` [src:KAL1]
27. **Mikä on rauhoitukset -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.rauhoitukset` [src:KAL1]
28. **Mikä on vesialueen lupa -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.vesialueen_lupa` [src:KAL1]
29. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:KAL1]
30. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:KAL1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 21
## AGENT 21: Kalantunnistaja
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: kalantunnistaja
  agent_name: Kalantunnistaja
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Tunnistaa lajit kuvasta/kuvauksesta
- Huhdasjärvi + Kaakkois-Suomen vesistöt
DECISION_METRICS_AND_THRESHOLDS:
  confidence_min_pct:
    value: 80
    action: <80% → pyydä lisäkuva (sivuprofiili + evät auki) tai mittaus
    source: src:KAT1
  protected_species:
    value: Järvitaimen, nieriä, ankerias
    action: VAPAUTA VEDESSÄ heti, ÄLÄ nosta. Dokumentoi kuva + GPS + aika. Ilmoita
      ELY-keskukselle.
    source: src:KAT2
  invasive_species:
    value: Hopearuutana (Carassius gibelio)
    action: EI takaisin veteen. Lopeta. Ilmoita ELY-keskukselle 2 pv sisällä.
    source: src:KAT2
  measurement:
    value: 'Kokonaispituus: kuono → pyrstön kärki'
    source: src:KAT1
  key_features_5:
    value: 1=evien lkm/sijainti, 2=suomut, 3=väri, 4=suun muoto, 5=kylkiviiva
    action: Jos ≤3 piirrettä nähtävissä → varmuus <80%, pyydä lisäkuva
    source: src:KAT1
  measurement_mm:
    value: Kokonaispituus kuono→pyrstön kärki, ±5 mm tarkkuus
    action: 'Mittaa AINA ennen päätöstä pitää/vapauttaa. Alamitta: hauki 400 mm, kuha
      420 mm.'
    source: src:KAT1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Kutuväritys muuttaa tunnistusta. Ahven/lahna kirkastuvat.'
  source: src:KAT1
- season: Kesä
  action: '[vko 22-35] Poikaset vaikeita — käytä evälaskentaa.'
  source: src:KAT1
- season: Syksy
  action: '[vko 36-48] Syöntiväritys ≠ kutuväri. Kuha/made aktiivisempia.'
  source: src:KAT1
- season: Talvi
  action: '[vko 49-13] Pilkkikalat: ahven vs kiiski (kiiski limaisempi).'
  source: src:KAT1
FAILURE_MODES:
- mode: Virheellinen lajintunnistus
  detection: Väärä alamittapäätös
  action: Mittaa AINA + valokuva ennen päätöstä
  source: src:KAT1
- mode: Suojeltu laji pyydetty
  detection: Järvitaimen/rauhoitettu
  action: Vapauta VEDESSÄ, dokumentoi, ilmoita ELY:lle
  source: src:KAT2
PROCESS_FLOWS:
- flow_id: FLOW_KALA_01
  trigger: confidence_min_pct ylittää kynnysarvon (80)
  action: <80% → pyydä lisäkuva (sivuprofiili + evät auki) tai mittaus
  output: Tilanneraportti
  source: src:KALA
- flow_id: FLOW_KALA_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Kutuväritys muuttaa tunnistusta. Ahven/lahna kirkastuvat.'
  output: Tarkistuslista
  source: src:KALA
- flow_id: FLOW_KALA_03
  trigger: 'Havaittu: Virheellinen lajintunnistus'
  action: Mittaa AINA + valokuva ennen päätöstä
  output: Poikkeamaraportti
  source: src:KALA
- flow_id: FLOW_KALA_04
  trigger: Säännöllinen heartbeat
  action: 'kalantunnistaja: rutiiniarviointi'
  output: Status-raportti
  source: src:KALA
KNOWLEDGE_TABLES:
  species:
  - laji: Ahven
    piirteet: Tummat poikkijuovat, punainen pyrstö, 2 selkäevää
  - laji: Hauki
    piirteet: Pitkä litteä kuono, vihertävä, selkäevä takana
  - laji: Kuha
    piirteet: Lasimaiset silmät, piikikkäät selkäevät
  - laji: Lahna
    piirteet: Korkea/litteä, pronssinvärinen aikuisena
  - laji: Särki
    piirteet: Hopeinen, punaiset silmät
  - laji: Made
    piirteet: Litteä pää, viikset, limainen, yöaktiivinen
COMPLIANCE_AND_LEGAL:
  protected: Järvitaimen rauhoitettu useilla alueilla [src:KAT2]
  invasive: Hopearuutanaa ei saa palauttaa veteen [src:KAT2]
UNCERTAINTY_NOTES:
- Risteymät (lahna×särki) tekevät tunnistamisesta vaikeaa — DNA ainoa varma keino.
SOURCE_REGISTRY:
  sources:
  - id: src:KAT1
    org: Luke
    title: Suomen kalat
    year: 2024
    url: https://www.luke.fi/
    supports: Lajintunnistus.
  - id: src:KAT2
    org: MMM/ELY
    title: Suojelu ja vieraslajit
    year: 2025
    url: https://kalastusrajoitus.fi/
    supports: Rauhoitukset, vieraslajit.
eval_questions:
- q: Miten ahven tunnistetaan?
  a_ref: KNOWLEDGE_TABLES.species[0].piirteet
  source: src:KAT1
- q: Miten hauki tunnistetaan?
  a_ref: KNOWLEDGE_TABLES.species[1].piirteet
  source: src:KAT1
- q: Mikä on kuha-tunnistuksen avain?
  a_ref: KNOWLEDGE_TABLES.species[2].piirteet
  source: src:KAT1
- q: Mikä on mateen erityispiirre?
  a_ref: KNOWLEDGE_TABLES.species[5].piirteet
  source: src:KAT1
- q: Miten risteymä tunnistetaan?
  a_ref: UNCERTAINTY_NOTES
  source: src:KAT1
- q: Saako hopearuutanan vapauttaa?
  a_ref: COMPLIANCE_AND_LEGAL.invasive
  source: src:KAT2
- q: Mitkä ovat 5 tunnistuspiirrettä?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.key_features_5
  source: src:KAT1
- q: Miten kutuväritys vaikuttaa?
  a_ref: SEASONAL_RULES[0].action
  source: src:KAT1
- q: Mikä on confidence min pct?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.confidence_min_pct
  source: src:KAT1
- q: Mitä tehdään kun confidence min pct ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.confidence_min_pct.action
  source: src:KAT1
- q: Mikä on protected species?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.protected_species
  source: src:KAT1
- q: Mitä tehdään kun protected species ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.protected_species.action
  source: src:KAT1
- q: Mikä on invasive species?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.invasive_species
  source: src:KAT1
- q: Mitä tehdään kun invasive species ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.invasive_species.action
  source: src:KAT1
- q: Mikä on measurement?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.measurement
  source: src:KAT1
- q: Mikä on key features 5?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.key_features_5
  source: src:KAT1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:KAT1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:KAT1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:KAT1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:KAT1
- q: Miten 'Virheellinen lajintunnistus' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:KAT1
- q: Mitä tehdään tilanteessa 'Virheellinen lajintunnistus'?
  a_ref: FAILURE_MODES[0].action
  source: src:KAT1
- q: Miten 'Suojeltu laji pyydetty' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:KAT1
- q: Mitä tehdään tilanteessa 'Suojeltu laji pyydetty'?
  a_ref: FAILURE_MODES[1].action
  source: src:KAT1
- q: Mikä on protected -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.protected
  source: src:KAT1
- q: Mikä on invasive -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.invasive
  source: src:KAT1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:KAT1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#11)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#12)?
  a_ref: ASSUMPTIONS
  source: src:KAT1
```

**sources.yaml:**
```yaml
sources:
- id: src:KAT1
  org: Luke
  title: Suomen kalat
  year: 2024
  url: https://www.luke.fi/
  supports: Lajintunnistus.
- id: src:KAT2
  org: MMM/ELY
  title: Suojelu ja vieraslajit
  year: 2025
  url: https://kalastusrajoitus.fi/
  supports: Rauhoitukset, vieraslajit.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Kalantunnistaja
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Tunnistaa lajit kuvasta/kuvauksesta
- Huhdasjärvi + Kaakkois-Suomen vesistöt

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| confidence_min_pct | 80 | <80% → pyydä lisäkuva (sivuprofiili + evät auki) tai mittaus | src:KAT1 |
| protected_species | Järvitaimen, nieriä, ankerias | VAPAUTA VEDESSÄ heti, ÄLÄ nosta. Dokumentoi kuva + GPS + aika. Ilmoita ELY-keskukselle. | src:KAT2 |
| invasive_species | Hopearuutana (Carassius gibelio) | EI takaisin veteen. Lopeta. Ilmoita ELY-keskukselle 2 pv sisällä. | src:KAT2 |
| measurement | Kokonaispituus: kuono → pyrstön kärki | — | src:KAT1 |
| key_features_5 | 1=evien lkm/sijainti, 2=suomut, 3=väri, 4=suun muoto, 5=kylkiviiva | Jos ≤3 piirrettä nähtävissä → varmuus <80%, pyydä lisäkuva | src:KAT1 |
| measurement_mm | Kokonaispituus kuono→pyrstön kärki, ±5 mm tarkkuus | Mittaa AINA ennen päätöstä pitää/vapauttaa. Alamitta: hauki 400 mm, kuha 420 mm. | src:KAT1 |

## Tietotaulukot

**species:**

| laji | piirteet |
| --- | --- |
| Ahven | Tummat poikkijuovat, punainen pyrstö, 2 selkäevää |
| Hauki | Pitkä litteä kuono, vihertävä, selkäevä takana |
| Kuha | Lasimaiset silmät, piikikkäät selkäevät |
| Lahna | Korkea/litteä, pronssinvärinen aikuisena |
| Särki | Hopeinen, punaiset silmät |
| Made | Litteä pää, viikset, limainen, yöaktiivinen |

## Prosessit

**FLOW_KALA_01:** confidence_min_pct ylittää kynnysarvon (80)
  → <80% → pyydä lisäkuva (sivuprofiili + evät auki) tai mittaus
  Tulos: Tilanneraportti

**FLOW_KALA_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Kutuväritys muuttaa tunnistusta. Ahven/lahna kirkastuvat.
  Tulos: Tarkistuslista

**FLOW_KALA_03:** Havaittu: Virheellinen lajintunnistus
  → Mittaa AINA + valokuva ennen päätöstä
  Tulos: Poikkeamaraportti

**FLOW_KALA_04:** Säännöllinen heartbeat
  → kalantunnistaja: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Kutuväritys muuttaa tunnistusta. Ahven/lahna kirkastuvat. | src:KAT1 |
| **Kesä** | [vko 22-35] Poikaset vaikeita — käytä evälaskentaa. | src:KAT1 |
| **Syksy** | [vko 36-48] Syöntiväritys ≠ kutuväri. Kuha/made aktiivisempia. | src:KAT1 |
| **Talvi** | [vko 49-13] Pilkkikalat: ahven vs kiiski (kiiski limaisempi). | src:KAT1 |

## Virhe- ja vaaratilanteet

### ⚠️ Virheellinen lajintunnistus
- **Havaitseminen:** Väärä alamittapäätös
- **Toimenpide:** Mittaa AINA + valokuva ennen päätöstä
- **Lähde:** src:KAT1

### ⚠️ Suojeltu laji pyydetty
- **Havaitseminen:** Järvitaimen/rauhoitettu
- **Toimenpide:** Vapauta VEDESSÄ, dokumentoi, ilmoita ELY:lle
- **Lähde:** src:KAT2

## Lait ja vaatimukset
- **protected:** Järvitaimen rauhoitettu useilla alueilla [src:KAT2]
- **invasive:** Hopearuutanaa ei saa palauttaa veteen [src:KAT2]

## Epävarmuudet
- Risteymät (lahna×särki) tekevät tunnistamisesta vaikeaa — DNA ainoa varma keino.

## Lähteet
- **src:KAT1**: Luke — *Suomen kalat* (2024) https://www.luke.fi/
- **src:KAT2**: MMM/ELY — *Suojelu ja vieraslajit* (2025) https://kalastusrajoitus.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Miten ahven tunnistetaan?**
    → `KNOWLEDGE_TABLES.species[0].piirteet` [src:KAT1]
 2. **Miten hauki tunnistetaan?**
    → `KNOWLEDGE_TABLES.species[1].piirteet` [src:KAT1]
 3. **Mikä on kuha-tunnistuksen avain?**
    → `KNOWLEDGE_TABLES.species[2].piirteet` [src:KAT1]
 4. **Mikä on mateen erityispiirre?**
    → `KNOWLEDGE_TABLES.species[5].piirteet` [src:KAT1]
 5. **Miten risteymä tunnistetaan?**
    → `UNCERTAINTY_NOTES` [src:KAT1]
 6. **Saako hopearuutanan vapauttaa?**
    → `COMPLIANCE_AND_LEGAL.invasive` [src:KAT2]
 7. **Mitkä ovat 5 tunnistuspiirrettä?**
    → `DECISION_METRICS_AND_THRESHOLDS.key_features_5` [src:KAT1]
 8. **Miten kutuväritys vaikuttaa?**
    → `SEASONAL_RULES[0].action` [src:KAT1]
 9. **Mikä on confidence min pct?**
    → `DECISION_METRICS_AND_THRESHOLDS.confidence_min_pct` [src:KAT1]
10. **Mitä tehdään kun confidence min pct ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.confidence_min_pct.action` [src:KAT1]
11. **Mikä on protected species?**
    → `DECISION_METRICS_AND_THRESHOLDS.protected_species` [src:KAT1]
12. **Mitä tehdään kun protected species ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.protected_species.action` [src:KAT1]
13. **Mikä on invasive species?**
    → `DECISION_METRICS_AND_THRESHOLDS.invasive_species` [src:KAT1]
14. **Mitä tehdään kun invasive species ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.invasive_species.action` [src:KAT1]
15. **Mikä on measurement?**
    → `DECISION_METRICS_AND_THRESHOLDS.measurement` [src:KAT1]
16. **Mikä on key features 5?**
    → `DECISION_METRICS_AND_THRESHOLDS.key_features_5` [src:KAT1]
17. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:KAT1]
18. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:KAT1]
19. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:KAT1]
20. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:KAT1]
21. **Miten 'Virheellinen lajintunnistus' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:KAT1]
22. **Mitä tehdään tilanteessa 'Virheellinen lajintunnistus'?**
    → `FAILURE_MODES[0].action` [src:KAT1]
23. **Miten 'Suojeltu laji pyydetty' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:KAT1]
24. **Mitä tehdään tilanteessa 'Suojeltu laji pyydetty'?**
    → `FAILURE_MODES[1].action` [src:KAT1]
25. **Mikä on protected -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.protected` [src:KAT1]
26. **Mikä on invasive -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.invasive` [src:KAT1]
27. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:KAT1]
28. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:KAT1]
29. **Miten tämä agentti kytkeytyy muihin agentteihin (#1)?**
    → `ASSUMPTIONS` [src:KAT1]
30. **Miten tämä agentti kytkeytyy muihin agentteihin (#2)?**
    → `ASSUMPTIONS` [src:KAT1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 22
## AGENT 22: Rantavahti
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: rantavahti
  agent_name: Rantavahti
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Huhdasjärven ranta
- Uimarien/veneilijöiden turvallisuus
DECISION_METRICS_AND_THRESHOLDS:
  swim_temp_min_c:
    value: 15
    action: <15°C → hypotermia-varoitus
    source: src:RV1
  wave_height_warning_cm:
    value: 30
    action: '>30 cm → pienveneilyvaroitus'
    source: src:RV1
  visibility_fog_m:
    value: 50
    action: <50 m → venetoiminta rajoitettu
    source: src:RV1
  thunderstorm_km:
    value: 10
    action: <10 km → VEDESTÄ POIS
    source: src:RV2
  child_depth_max_cm:
    value: 30
    action: Lapsi <10v aina seurassa vedessä
    source: src:RV1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Jäiden lähtö → ranta vaarallinen. Ei uintikautta.'
  source: src:RV1
- season: Kesä
  action: '[vko 22-35] Uintikausi. Sinilevätarkistus päivittäin. Pelastusrengas paikallaan.'
  source: src:RV1
- season: Syksy
  action: '[vko 36-48] Vesi viilenee → hypotermiaviski. Veneilyn lopetus.'
  source: src:RV1
- season: Talvi
  action: Avantouinti valvotusti. Max 1-2 min. Jääasiantuntijalta kantavuus.
  source: src:RV1
FAILURE_MODES:
- mode: Hukkumisvaara
  detection: Henkilö vaikeuksissa vedessä
  action: Heitä pelastusrengas, soita 112, ÄLÄ mene veteen yksin
  source: src:RV2
- mode: Sinilevämyrkytys
  detection: Iho-oireita uinnin jälkeen
  action: Huuhtele, myrkytystietokeskus 0800 147 111
  source: src:RV2
PROCESS_FLOWS:
- flow_id: FLOW_RANT_01
  trigger: swim_temp_min_c ylittää kynnysarvon (15)
  action: <15°C → hypotermia-varoitus
  output: Tilanneraportti
  source: src:RANT
- flow_id: FLOW_RANT_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Jäiden lähtö → ranta vaarallinen. Ei uintikautta.'
  output: Tarkistuslista
  source: src:RANT
- flow_id: FLOW_RANT_03
  trigger: 'Havaittu: Hukkumisvaara'
  action: Heitä pelastusrengas, soita 112, ÄLÄ mene veteen yksin
  output: Poikkeamaraportti
  source: src:RANT
- flow_id: FLOW_RANT_04
  trigger: Säännöllinen heartbeat
  action: 'rantavahti: rutiiniarviointi'
  output: Status-raportti
  source: src:RANT
KNOWLEDGE_TABLES:
- table_id: TBL_RANT_01
  title: Rantavahti — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: swim_temp_min_c
    value: '15'
    action: <15°C → hypotermia-varoitus
  - metric: wave_height_warning_cm
    value: '30'
    action: '>30 cm → pienveneilyvaroitus'
  - metric: visibility_fog_m
    value: '50'
    action: <50 m → venetoiminta rajoitettu
  - metric: thunderstorm_km
    value: '10'
    action: <10 km → VEDESTÄ POIS
  - metric: child_depth_max_cm
    value: '30'
    action: Lapsi <10v aina seurassa vedessä
  source: src:RANT
COMPLIANCE_AND_LEGAL:
  pelastusvaline: Rannanpitäjän velvollisuus pelastusvälineeseen [src:RV2]
UNCERTAINTY_NOTES:
- Pienen järven aallokko riippuu tuulensuunnasta.
SOURCE_REGISTRY:
  sources:
  - id: src:RV1
    org: SUH
    title: Vesiturvallisuus
    year: 2025
    url: https://www.suh.fi/
    supports: Uintiturvallisuus.
  - id: src:RV2
    org: Pelastuslaitos/THL
    title: Hätäohjeet
    year: 2025
    url: https://www.112.fi/
    supports: Ensiapu, hätänumerot.
eval_questions:
- q: Mikä on swim temp min c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.swim_temp_min_c
  source: src:RV1
- q: Mitä tehdään kun swim temp min c ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.swim_temp_min_c.action
  source: src:RV1
- q: Mikä on wave height warning cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wave_height_warning_cm
  source: src:RV1
- q: Mitä tehdään kun wave height warning cm ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wave_height_warning_cm.action
  source: src:RV1
- q: Mikä on visibility fog m?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.visibility_fog_m
  source: src:RV1
- q: Mitä tehdään kun visibility fog m ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.visibility_fog_m.action
  source: src:RV1
- q: Mikä on thunderstorm km?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thunderstorm_km
  source: src:RV1
- q: Mitä tehdään kun thunderstorm km ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.thunderstorm_km.action
  source: src:RV1
- q: Mikä on child depth max cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.child_depth_max_cm
  source: src:RV1
- q: Mitä tehdään kun child depth max cm ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.child_depth_max_cm.action
  source: src:RV1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:RV1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:RV1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:RV1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:RV1
- q: Miten 'Hukkumisvaara' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:RV1
- q: Mitä tehdään tilanteessa 'Hukkumisvaara'?
  a_ref: FAILURE_MODES[0].action
  source: src:RV1
- q: Miten 'Sinilevämyrkytys' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:RV1
- q: Mitä tehdään tilanteessa 'Sinilevämyrkytys'?
  a_ref: FAILURE_MODES[1].action
  source: src:RV1
- q: Mikä on pelastusvaline -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.pelastusvaline
  source: src:RV1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:RV1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#11)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#12)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#13)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#14)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#15)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#16)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#17)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#18)?
  a_ref: ASSUMPTIONS
  source: src:RV1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#19)?
  a_ref: ASSUMPTIONS
  source: src:RV1
```

**sources.yaml:**
```yaml
sources:
- id: src:RV1
  org: SUH
  title: Vesiturvallisuus
  year: 2025
  url: https://www.suh.fi/
  supports: Uintiturvallisuus.
- id: src:RV2
  org: Pelastuslaitos/THL
  title: Hätäohjeet
  year: 2025
  url: https://www.112.fi/
  supports: Ensiapu, hätänumerot.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Rantavahti
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Huhdasjärven ranta
- Uimarien/veneilijöiden turvallisuus

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| swim_temp_min_c | 15 | <15°C → hypotermia-varoitus | src:RV1 |
| wave_height_warning_cm | 30 | >30 cm → pienveneilyvaroitus | src:RV1 |
| visibility_fog_m | 50 | <50 m → venetoiminta rajoitettu | src:RV1 |
| thunderstorm_km | 10 | <10 km → VEDESTÄ POIS | src:RV2 |
| child_depth_max_cm | 30 | Lapsi <10v aina seurassa vedessä | src:RV1 |

## Tietotaulukot

**Rantavahti — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| swim_temp_min_c | 15 | <15°C → hypotermia-varoitus |
| wave_height_warning_cm | 30 | >30 cm → pienveneilyvaroitus |
| visibility_fog_m | 50 | <50 m → venetoiminta rajoitettu |
| thunderstorm_km | 10 | <10 km → VEDESTÄ POIS |
| child_depth_max_cm | 30 | Lapsi <10v aina seurassa vedessä |

## Prosessit

**FLOW_RANT_01:** swim_temp_min_c ylittää kynnysarvon (15)
  → <15°C → hypotermia-varoitus
  Tulos: Tilanneraportti

**FLOW_RANT_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Jäiden lähtö → ranta vaarallinen. Ei uintikautta.
  Tulos: Tarkistuslista

**FLOW_RANT_03:** Havaittu: Hukkumisvaara
  → Heitä pelastusrengas, soita 112, ÄLÄ mene veteen yksin
  Tulos: Poikkeamaraportti

**FLOW_RANT_04:** Säännöllinen heartbeat
  → rantavahti: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Jäiden lähtö → ranta vaarallinen. Ei uintikautta. | src:RV1 |
| **Kesä** | [vko 22-35] Uintikausi. Sinilevätarkistus päivittäin. Pelastusrengas paikallaan. | src:RV1 |
| **Syksy** | [vko 36-48] Vesi viilenee → hypotermiaviski. Veneilyn lopetus. | src:RV1 |
| **Talvi** | Avantouinti valvotusti. Max 1-2 min. Jääasiantuntijalta kantavuus. | src:RV1 |

## Virhe- ja vaaratilanteet

### ⚠️ Hukkumisvaara
- **Havaitseminen:** Henkilö vaikeuksissa vedessä
- **Toimenpide:** Heitä pelastusrengas, soita 112, ÄLÄ mene veteen yksin
- **Lähde:** src:RV2

### ⚠️ Sinilevämyrkytys
- **Havaitseminen:** Iho-oireita uinnin jälkeen
- **Toimenpide:** Huuhtele, myrkytystietokeskus 0800 147 111
- **Lähde:** src:RV2

## Lait ja vaatimukset
- **pelastusvaline:** Rannanpitäjän velvollisuus pelastusvälineeseen [src:RV2]

## Epävarmuudet
- Pienen järven aallokko riippuu tuulensuunnasta.

## Lähteet
- **src:RV1**: SUH — *Vesiturvallisuus* (2025) https://www.suh.fi/
- **src:RV2**: Pelastuslaitos/THL — *Hätäohjeet* (2025) https://www.112.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on swim temp min c?**
    → `DECISION_METRICS_AND_THRESHOLDS.swim_temp_min_c` [src:RV1]
 2. **Mitä tehdään kun swim temp min c ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.swim_temp_min_c.action` [src:RV1]
 3. **Mikä on wave height warning cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.wave_height_warning_cm` [src:RV1]
 4. **Mitä tehdään kun wave height warning cm ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.wave_height_warning_cm.action` [src:RV1]
 5. **Mikä on visibility fog m?**
    → `DECISION_METRICS_AND_THRESHOLDS.visibility_fog_m` [src:RV1]
 6. **Mitä tehdään kun visibility fog m ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.visibility_fog_m.action` [src:RV1]
 7. **Mikä on thunderstorm km?**
    → `DECISION_METRICS_AND_THRESHOLDS.thunderstorm_km` [src:RV1]
 8. **Mitä tehdään kun thunderstorm km ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.thunderstorm_km.action` [src:RV1]
 9. **Mikä on child depth max cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.child_depth_max_cm` [src:RV1]
10. **Mitä tehdään kun child depth max cm ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.child_depth_max_cm.action` [src:RV1]
11. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:RV1]
12. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:RV1]
13. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:RV1]
14. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:RV1]
15. **Miten 'Hukkumisvaara' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:RV1]
16. **Mitä tehdään tilanteessa 'Hukkumisvaara'?**
    → `FAILURE_MODES[0].action` [src:RV1]
17. **Miten 'Sinilevämyrkytys' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:RV1]
18. **Mitä tehdään tilanteessa 'Sinilevämyrkytys'?**
    → `FAILURE_MODES[1].action` [src:RV1]
19. **Mikä on pelastusvaline -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.pelastusvaline` [src:RV1]
20. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:RV1]
21. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:RV1]
22. **Miten tämä agentti kytkeytyy muihin agentteihin (#1)?**
    → `ASSUMPTIONS` [src:RV1]
23. **Miten tämä agentti kytkeytyy muihin agentteihin (#2)?**
    → `ASSUMPTIONS` [src:RV1]
24. **Miten tämä agentti kytkeytyy muihin agentteihin (#3)?**
    → `ASSUMPTIONS` [src:RV1]
25. **Miten tämä agentti kytkeytyy muihin agentteihin (#4)?**
    → `ASSUMPTIONS` [src:RV1]
26. **Miten tämä agentti kytkeytyy muihin agentteihin (#5)?**
    → `ASSUMPTIONS` [src:RV1]
27. **Miten tämä agentti kytkeytyy muihin agentteihin (#6)?**
    → `ASSUMPTIONS` [src:RV1]
28. **Miten tämä agentti kytkeytyy muihin agentteihin (#7)?**
    → `ASSUMPTIONS` [src:RV1]
29. **Miten tämä agentti kytkeytyy muihin agentteihin (#8)?**
    → `ASSUMPTIONS` [src:RV1]
30. **Miten tämä agentti kytkeytyy muihin agentteihin (#9)?**
    → `ASSUMPTIONS` [src:RV1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 23
## AGENT 23: Jääasiantuntija
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: jaaasiantuntija
  agent_name: Jääasiantuntija
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Huhdasjärven jää
- Pilkintä, retkiluistelu, moottorikelkkailu
DECISION_METRICS_AND_THRESHOLDS:
  ice_walk_cm:
    value: 5
    action: ≥5 cm teräsjää → jalankulku
    source: src:JA1
  ice_snowmobile_cm:
    value: 15
    action: ≥15 cm → kelkka
    source: src:JA1
  ice_car_cm:
    value: 40
    action: ≥40 cm → auto (EI suositella)
    source: src:JA1
  weak_ice_signs:
    value: Tumma jää, virtapaikat, kaislikon reuna
    action: VÄLTÄ aina, mittaa 50m välein
    source: src:JA1
  spring_deterioration:
    value: Maaliskuun loppu (vrk-T >0°C)
    action: LOPETA jäällä liikkuminen kun yöpakkaset loppuvat
    source: src:JA1
SEASONAL_RULES:
- season: Syksy
  action: '[vko 36-48] Jää muodostuu. Ensijää petollinen — mittaa AINA.'
  source: src:JA1
- season: Talvi
  action: '[vko 49-13] Vahvimmillaan. Lumikuorma heikentää. Kohvajää = puolet teräsjään
    kantavuudesta.'
  source: src:JA1
- season: Kevät
  action: '[vko 14-22] Haurastuu nopeasti. Virtapaikat sulavat ensin.'
  source: src:JA1
- season: Kesä
  action: '[vko 22-35] Ei jäätä.'
  source: src:JA1
FAILURE_MODES:
- mode: Jään murtuminen
  detection: Ratinaa, vesi pinnalle
  action: MAHALLEEN, ryömi taaksepäin, levitä paino, soita 112
  source: src:JA1
- mode: Henkilö pudonnut jäihin
  detection: Avanto
  action: Heitä köysi/oksa, ÄLÄ mene heikolle jäälle, soita 112
  source: src:JA2
PROCESS_FLOWS:
- flow_id: FLOW_JAAA_01
  trigger: ice_walk_cm ylittää kynnysarvon (5)
  action: ≥5 cm teräsjää → jalankulku
  output: Tilanneraportti
  source: src:JAAA
- flow_id: FLOW_JAAA_02
  trigger: 'Kausi vaihtuu: Syksy'
  action: '[vko 36-48] Jää muodostuu. Ensijää petollinen — mittaa AINA.'
  output: Tarkistuslista
  source: src:JAAA
- flow_id: FLOW_JAAA_03
  trigger: 'Havaittu: Jään murtuminen'
  action: MAHALLEEN, ryömi taaksepäin, levitä paino, soita 112
  output: Poikkeamaraportti
  source: src:JAAA
- flow_id: FLOW_JAAA_04
  trigger: Säännöllinen heartbeat
  action: 'jaaasiantuntija: rutiiniarviointi'
  output: Status-raportti
  source: src:JAAA
KNOWLEDGE_TABLES:
- table_id: TBL_JAAA_01
  title: Jääasiantuntija — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: ice_walk_cm
    value: '5'
    action: ≥5 cm teräsjää → jalankulku
  - metric: ice_snowmobile_cm
    value: '15'
    action: ≥15 cm → kelkka
  - metric: ice_car_cm
    value: '40'
    action: ≥40 cm → auto (EI suositella)
  - metric: weak_ice_signs
    value: Tumma jää, virtapaikat, kaislikon reuna
    action: VÄLTÄ aina, mittaa 50m välein
  - metric: spring_deterioration
    value: Maaliskuun loppu (vrk-T >0°C)
    action: LOPETA jäällä liikkuminen kun yöpakkaset loppuvat
  source: src:JAAA
COMPLIANCE_AND_LEGAL:
  vastuu: Jäällä omalla vastuulla [src:JA1]
UNCERTAINTY_NOTES:
- Jään paksuus vaihtelee samalla järvellä huomattavasti.
- Kohvajää kantaa ~50% teräsjään verran.
SOURCE_REGISTRY:
  sources:
  - id: src:JA1
    org: SUH/Pelastuslaitos
    title: Jääturvallisuus
    year: 2025
    url: https://www.suh.fi/
    supports: Jäänpaksuus, mittaus.
  - id: src:JA2
    org: Pelastuslaitos
    title: Jäähänputoaminen
    year: 2025
    url: https://pelastustoimi.fi/
    supports: Pelastustoimet.
eval_questions:
- q: Mikä on ice walk cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ice_walk_cm
  source: src:JA1
- q: Mitä tehdään kun ice walk cm ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ice_walk_cm.action
  source: src:JA1
- q: Mikä on ice snowmobile cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ice_snowmobile_cm
  source: src:JA1
- q: Mitä tehdään kun ice snowmobile cm ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ice_snowmobile_cm.action
  source: src:JA1
- q: Mikä on ice car cm?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ice_car_cm
  source: src:JA1
- q: Mitä tehdään kun ice car cm ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.ice_car_cm.action
  source: src:JA1
- q: Mikä on weak ice signs?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.weak_ice_signs
  source: src:JA1
- q: Mitä tehdään kun weak ice signs ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.weak_ice_signs.action
  source: src:JA1
- q: Mikä on spring deterioration?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.spring_deterioration
  source: src:JA1
- q: Mitä tehdään kun spring deterioration ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.spring_deterioration.action
  source: src:JA1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:JA1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:JA1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:JA1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:JA1
- q: Miten 'Jään murtuminen' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:JA1
- q: Mitä tehdään tilanteessa 'Jään murtuminen'?
  a_ref: FAILURE_MODES[0].action
  source: src:JA1
- q: Miten 'Henkilö pudonnut jäihin' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:JA1
- q: Mitä tehdään tilanteessa 'Henkilö pudonnut jäihin'?
  a_ref: FAILURE_MODES[1].action
  source: src:JA1
- q: Mikä on vastuu -vaatimus?
  a_ref: COMPLIANCE_AND_LEGAL.vastuu
  source: src:JA1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:JA1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#11)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#12)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#13)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#14)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#15)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#16)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#17)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#18)?
  a_ref: ASSUMPTIONS
  source: src:JA1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#19)?
  a_ref: ASSUMPTIONS
  source: src:JA1
```

**sources.yaml:**
```yaml
sources:
- id: src:JA1
  org: SUH/Pelastuslaitos
  title: Jääturvallisuus
  year: 2025
  url: https://www.suh.fi/
  supports: Jäänpaksuus, mittaus.
- id: src:JA2
  org: Pelastuslaitos
  title: Jäähänputoaminen
  year: 2025
  url: https://pelastustoimi.fi/
  supports: Pelastustoimet.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Jääasiantuntija
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Huhdasjärven jää
- Pilkintä, retkiluistelu, moottorikelkkailu

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| ice_walk_cm | 5 | ≥5 cm teräsjää → jalankulku | src:JA1 |
| ice_snowmobile_cm | 15 | ≥15 cm → kelkka | src:JA1 |
| ice_car_cm | 40 | ≥40 cm → auto (EI suositella) | src:JA1 |
| weak_ice_signs | Tumma jää, virtapaikat, kaislikon reuna | VÄLTÄ aina, mittaa 50m välein | src:JA1 |
| spring_deterioration | Maaliskuun loppu (vrk-T >0°C) | LOPETA jäällä liikkuminen kun yöpakkaset loppuvat | src:JA1 |

## Tietotaulukot

**Jääasiantuntija — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| ice_walk_cm | 5 | ≥5 cm teräsjää → jalankulku |
| ice_snowmobile_cm | 15 | ≥15 cm → kelkka |
| ice_car_cm | 40 | ≥40 cm → auto (EI suositella) |
| weak_ice_signs | Tumma jää, virtapaikat, kaislikon reuna | VÄLTÄ aina, mittaa 50m välein |
| spring_deterioration | Maaliskuun loppu (vrk-T >0°C) | LOPETA jäällä liikkuminen kun yöpakkaset loppuvat |

## Prosessit

**FLOW_JAAA_01:** ice_walk_cm ylittää kynnysarvon (5)
  → ≥5 cm teräsjää → jalankulku
  Tulos: Tilanneraportti

**FLOW_JAAA_02:** Kausi vaihtuu: Syksy
  → [vko 36-48] Jää muodostuu. Ensijää petollinen — mittaa AINA.
  Tulos: Tarkistuslista

**FLOW_JAAA_03:** Havaittu: Jään murtuminen
  → MAHALLEEN, ryömi taaksepäin, levitä paino, soita 112
  Tulos: Poikkeamaraportti

**FLOW_JAAA_04:** Säännöllinen heartbeat
  → jaaasiantuntija: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Syksy** | [vko 36-48] Jää muodostuu. Ensijää petollinen — mittaa AINA. | src:JA1 |
| **Talvi** | [vko 49-13] Vahvimmillaan. Lumikuorma heikentää. Kohvajää = puolet teräsjään kantavuudesta. | src:JA1 |
| **Kevät** | [vko 14-22] Haurastuu nopeasti. Virtapaikat sulavat ensin. | src:JA1 |
| **Kesä** | [vko 22-35] Ei jäätä. | src:JA1 |

## Virhe- ja vaaratilanteet

### ⚠️ Jään murtuminen
- **Havaitseminen:** Ratinaa, vesi pinnalle
- **Toimenpide:** MAHALLEEN, ryömi taaksepäin, levitä paino, soita 112
- **Lähde:** src:JA1

### ⚠️ Henkilö pudonnut jäihin
- **Havaitseminen:** Avanto
- **Toimenpide:** Heitä köysi/oksa, ÄLÄ mene heikolle jäälle, soita 112
- **Lähde:** src:JA2

## Lait ja vaatimukset
- **vastuu:** Jäällä omalla vastuulla [src:JA1]

## Epävarmuudet
- Jään paksuus vaihtelee samalla järvellä huomattavasti.
- Kohvajää kantaa ~50% teräsjään verran.

## Lähteet
- **src:JA1**: SUH/Pelastuslaitos — *Jääturvallisuus* (2025) https://www.suh.fi/
- **src:JA2**: Pelastuslaitos — *Jäähänputoaminen* (2025) https://pelastustoimi.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on ice walk cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.ice_walk_cm` [src:JA1]
 2. **Mitä tehdään kun ice walk cm ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.ice_walk_cm.action` [src:JA1]
 3. **Mikä on ice snowmobile cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.ice_snowmobile_cm` [src:JA1]
 4. **Mitä tehdään kun ice snowmobile cm ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.ice_snowmobile_cm.action` [src:JA1]
 5. **Mikä on ice car cm?**
    → `DECISION_METRICS_AND_THRESHOLDS.ice_car_cm` [src:JA1]
 6. **Mitä tehdään kun ice car cm ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.ice_car_cm.action` [src:JA1]
 7. **Mikä on weak ice signs?**
    → `DECISION_METRICS_AND_THRESHOLDS.weak_ice_signs` [src:JA1]
 8. **Mitä tehdään kun weak ice signs ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.weak_ice_signs.action` [src:JA1]
 9. **Mikä on spring deterioration?**
    → `DECISION_METRICS_AND_THRESHOLDS.spring_deterioration` [src:JA1]
10. **Mitä tehdään kun spring deterioration ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.spring_deterioration.action` [src:JA1]
11. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:JA1]
12. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:JA1]
13. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:JA1]
14. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:JA1]
15. **Miten 'Jään murtuminen' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:JA1]
16. **Mitä tehdään tilanteessa 'Jään murtuminen'?**
    → `FAILURE_MODES[0].action` [src:JA1]
17. **Miten 'Henkilö pudonnut jäihin' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:JA1]
18. **Mitä tehdään tilanteessa 'Henkilö pudonnut jäihin'?**
    → `FAILURE_MODES[1].action` [src:JA1]
19. **Mikä on vastuu -vaatimus?**
    → `COMPLIANCE_AND_LEGAL.vastuu` [src:JA1]
20. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:JA1]
21. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:JA1]
22. **Miten tämä agentti kytkeytyy muihin agentteihin (#1)?**
    → `ASSUMPTIONS` [src:JA1]
23. **Miten tämä agentti kytkeytyy muihin agentteihin (#2)?**
    → `ASSUMPTIONS` [src:JA1]
24. **Miten tämä agentti kytkeytyy muihin agentteihin (#3)?**
    → `ASSUMPTIONS` [src:JA1]
25. **Miten tämä agentti kytkeytyy muihin agentteihin (#4)?**
    → `ASSUMPTIONS` [src:JA1]
26. **Miten tämä agentti kytkeytyy muihin agentteihin (#5)?**
    → `ASSUMPTIONS` [src:JA1]
27. **Miten tämä agentti kytkeytyy muihin agentteihin (#6)?**
    → `ASSUMPTIONS` [src:JA1]
28. **Miten tämä agentti kytkeytyy muihin agentteihin (#7)?**
    → `ASSUMPTIONS` [src:JA1]
29. **Miten tämä agentti kytkeytyy muihin agentteihin (#8)?**
    → `ASSUMPTIONS` [src:JA1]
30. **Miten tämä agentti kytkeytyy muihin agentteihin (#9)?**
    → `ASSUMPTIONS` [src:JA1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 24
## AGENT 24: Meteorologi
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: meteorologi
  agent_name: Meteorologi
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Ilmatieteen laitos + paikallinen sääasema Korvenrannassa
- Säädata kaikille agenteille
DECISION_METRICS_AND_THRESHOLDS:
  temperature_c:
    value: Jatkuva seuranta
    thresholds:
      frost: 0
      heat: 25
      extreme_cold: -25
      extreme_heat: 30
    action: T<0°C → hallavaroitus hortonomille+tarhaajalle. T>25°C → hellevaroitus.
      T<-25°C → putkijäätymisvaara LVI:lle.
    source: src:ME1
  wind_ms:
    value: Jatkuva seuranta
    thresholds:
      moderate: 8
      strong: 14
      storm: 21
    action: '>8 m/s → mehiläisten lentoaktiivisuus laskee. >14 m/s → varoitus ulkoagenteille.
      >21 m/s → MYRSKY, myrskyvaroittajalle.'
    source: src:ME1
  precip_mm_h:
    value: Seuranta
    thresholds:
      light: 0.5
      moderate: 4
      heavy: 8
    action: '>0.5 mm/h → mehiläiset eivät lennä. >4 mm/h → tulvariski salaojille.
      >8 mm/h → veden nousu.'
    source: src:ME1
  humidity_rh:
    value: Seuranta 40-85% normaali
    thresholds:
      dry: 30
      damp: 85
    action: <30% RH → kuivuusvaara kasveille, ilmoita hortonomille. >85% → homeriski,
      ilmoita timpurille.
    source: src:ME1
  pressure_hpa:
    value: 1010-1025 hPa normaali
    thresholds:
      low: 1000
      high: 1035
    action: <1000 hPa + laskeva trendi → myrsky tulossa, ilmoita myrskyvaroittajalle.
      Laskeva paine → kalastusoppaalle (syönti parantuu).
    source: src:ME1
  uv_index:
    value: Kesällä 0-8
    thresholds:
      moderate: 3
      high: 6
      very_high: 8
    action: UV>6 → suojautumisvaroitus. UV>8 → rajoita ulkotyö klo 11-15.
    source: src:ME1
SEASONAL_RULES:
- season: Kevät
  action: Hallavaroitukset kun T<0°C yöllä (huhti-touko). Tulvariskin seuranta lumien
    sulaessa. Jäiden lähtö vko 16-19 Kouvolassa.
  source: src:ME1
- season: Kesä
  action: Ukkosvaroitukset (kesä-elo). Hellevaroitus T>25°C yli 3 pv. UV>6 klo 11-15.
    Nektarieritys optimaalinen T>18°C + RH 50-80%.
  source: src:ME1
- season: Syksy
  action: 'Myrskykausi loka-joulukuu: tuulivaroitukset >14 m/s. Ensipakkaset tyypillisesti
    vko 40-44. Sähkökatkosriski myrskyssä.'
  source: src:ME1
- season: Talvi
  action: Pakkasvaroitus T<-25°C (putkijäätyminen). Liukkausvaroitus T lähellä 0°C.
    Lumikuormavaroitus >150 kg/m². Häkävaara inversiossa.
  source: src:ME1
FAILURE_MODES:
- mode: Sääasema offline
  detection: Ei dataa >30 min
  action: 'Fallback: Ilmatieteen laitos API, ilmoita laitehuoltajalle'
  source: src:ME1
- mode: Ennustevirhe >5°C
  detection: Toteutunut vs. ennuste
  action: Päivitä agenttien tilannekuva reaaliajassa
  source: src:ME1
PROCESS_FLOWS:
- flow_id: FLOW_METE_01
  trigger: temperature_c ylittää kynnysarvon (Jatkuva seuranta)
  action: T<0°C → hallavaroitus hortonomille+tarhaajalle. T>25°C → hellevaroitus.
    T<-25°C → putkijäätymisvaara LVI:lle.
  output: Tilanneraportti
  source: src:METE
- flow_id: FLOW_METE_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: Hallavaroitukset kun T<0°C yöllä (huhti-touko). Tulvariskin seuranta lumien
    sulaessa. Jäiden lähtö v
  output: Tarkistuslista
  source: src:METE
- flow_id: FLOW_METE_03
  trigger: 'Havaittu: Sääasema offline'
  action: 'Fallback: Ilmatieteen laitos API, ilmoita laitehuoltajalle'
  output: Poikkeamaraportti
  source: src:METE
- flow_id: FLOW_METE_04
  trigger: Säännöllinen heartbeat
  action: 'meteorologi: rutiiniarviointi'
  output: Status-raportti
  source: src:METE
KNOWLEDGE_TABLES:
- table_id: TBL_METE_01
  title: Meteorologi — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: temperature_c
    value: Jatkuva seuranta
    action: 'T<0°C → hallavaroitus hortonomille+tarhaajalle. T>25°C → hellevaroitus.
      T<-25°C '
  - metric: wind_ms
    value: Jatkuva seuranta
    action: '>8 m/s → mehiläisten lentoaktiivisuus laskee. >14 m/s → varoitus ulkoagenteille.'
  - metric: precip_mm_h
    value: Seuranta
    action: '>0.5 mm/h → mehiläiset eivät lennä. >4 mm/h → tulvariski salaojille.
      >8 mm/h → v'
  - metric: humidity_rh
    value: Seuranta 40-85% normaali
    action: <30% RH → kuivuusvaara kasveille, ilmoita hortonomille. >85% → homeriski,
      ilmoit
  - metric: pressure_hpa
    value: 1010-1025 hPa normaali
    action: <1000 hPa + laskeva trendi → myrsky tulossa, ilmoita myrskyvaroittajalle.
      Laskev
  - metric: uv_index
    value: Kesällä 0-8
    action: UV>6 → suojautumisvaroitus. UV>8 → rajoita ulkotyö klo 11-15.
  source: src:METE
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Paikallinen sää voi poiketa (järvi/metsäefekti).
- Tarkkuus heikkenee >3 pv ennusteissa.
SOURCE_REGISTRY:
  sources:
  - id: src:ME1
    org: Ilmatieteen laitos
    title: Sääennusteet ja varoitukset
    year: 2026
    url: https://www.ilmatieteenlaitos.fi/
    supports: Säädata, ennusteet, varoitusrajat.
eval_questions:
- q: Mikä on temperature c?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.temperature_c
  source: src:ME1
- q: Mikä on wind ms?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wind_ms
  source: src:ME1
- q: Mikä on precip mm h?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.precip_mm_h
  source: src:ME1
- q: Mikä on humidity rh?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.humidity_rh
  source: src:ME1
- q: Mikä on pressure hpa?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.pressure_hpa
  source: src:ME1
- q: Mikä on uv index?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.uv_index
  source: src:ME1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:ME1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:ME1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:ME1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:ME1
- q: Miten 'Sääasema offline' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:ME1
- q: Mitä tehdään tilanteessa 'Sääasema offline'?
  a_ref: FAILURE_MODES[0].action
  source: src:ME1
- q: Miten 'Ennustevirhe >5°C' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:ME1
- q: Mitä tehdään tilanteessa 'Ennustevirhe >5°C'?
  a_ref: FAILURE_MODES[1].action
  source: src:ME1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:ME1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#11)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#12)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#13)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#14)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#15)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#16)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#17)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#18)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#19)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#20)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#21)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#22)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#23)?
  a_ref: ASSUMPTIONS
  source: src:ME1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#24)?
  a_ref: ASSUMPTIONS
  source: src:ME1
```

**sources.yaml:**
```yaml
sources:
- id: src:ME1
  org: Ilmatieteen laitos
  title: Sääennusteet ja varoitukset
  year: 2026
  url: https://www.ilmatieteenlaitos.fi/
  supports: Säädata, ennusteet, varoitusrajat.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Meteorologi
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Ilmatieteen laitos + paikallinen sääasema Korvenrannassa
- Säädata kaikille agenteille

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| temperature_c | Jatkuva seuranta | Kynnykset: frost=0, heat=25, extreme_cold=-25, extreme_heat=30 | src:ME1 |
| wind_ms | Jatkuva seuranta | Kynnykset: moderate=8, strong=14, storm=21 | src:ME1 |
| precip_mm_h | Seuranta | Kynnykset: light=0.5, moderate=4, heavy=8 | src:ME1 |
| humidity_rh | Seuranta 40-85% normaali | Kynnykset: dry=30, damp=85 | src:ME1 |
| pressure_hpa | 1010-1025 hPa normaali | Kynnykset: low=1000, high=1035 | src:ME1 |
| uv_index | Kesällä 0-8 | Kynnykset: moderate=3, high=6, very_high=8 | src:ME1 |

## Tietotaulukot

**Meteorologi — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| temperature_c | Jatkuva seuranta | T<0°C → hallavaroitus hortonomille+tarhaajalle. T>25°C → hellevaroitus. T<-25°C  |
| wind_ms | Jatkuva seuranta | >8 m/s → mehiläisten lentoaktiivisuus laskee. >14 m/s → varoitus ulkoagenteille. |
| precip_mm_h | Seuranta | >0.5 mm/h → mehiläiset eivät lennä. >4 mm/h → tulvariski salaojille. >8 mm/h → v |
| humidity_rh | Seuranta 40-85% normaali | <30% RH → kuivuusvaara kasveille, ilmoita hortonomille. >85% → homeriski, ilmoit |
| pressure_hpa | 1010-1025 hPa normaali | <1000 hPa + laskeva trendi → myrsky tulossa, ilmoita myrskyvaroittajalle. Laskev |
| uv_index | Kesällä 0-8 | UV>6 → suojautumisvaroitus. UV>8 → rajoita ulkotyö klo 11-15. |

## Prosessit

**FLOW_METE_01:** temperature_c ylittää kynnysarvon (Jatkuva seuranta)
  → T<0°C → hallavaroitus hortonomille+tarhaajalle. T>25°C → hellevaroitus. T<-25°C → putkijäätymisvaara LVI:lle.
  Tulos: Tilanneraportti

**FLOW_METE_02:** Kausi vaihtuu: Kevät
  → Hallavaroitukset kun T<0°C yöllä (huhti-touko). Tulvariskin seuranta lumien sulaessa. Jäiden lähtö v
  Tulos: Tarkistuslista

**FLOW_METE_03:** Havaittu: Sääasema offline
  → Fallback: Ilmatieteen laitos API, ilmoita laitehuoltajalle
  Tulos: Poikkeamaraportti

**FLOW_METE_04:** Säännöllinen heartbeat
  → meteorologi: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | Hallavaroitukset kun T<0°C yöllä (huhti-touko). Tulvariskin seuranta lumien sulaessa. Jäiden lähtö vko 16-19 Kouvolassa. | src:ME1 |
| **Kesä** | Ukkosvaroitukset (kesä-elo). Hellevaroitus T>25°C yli 3 pv. UV>6 klo 11-15. Nektarieritys optimaalinen T>18°C + RH 50-80%. | src:ME1 |
| **Syksy** | Myrskykausi loka-joulukuu: tuulivaroitukset >14 m/s. Ensipakkaset tyypillisesti vko 40-44. Sähkökatkosriski myrskyssä. | src:ME1 |
| **Talvi** | Pakkasvaroitus T<-25°C (putkijäätyminen). Liukkausvaroitus T lähellä 0°C. Lumikuormavaroitus >150 kg/m². Häkävaara inversiossa. | src:ME1 |

## Virhe- ja vaaratilanteet

### ⚠️ Sääasema offline
- **Havaitseminen:** Ei dataa >30 min
- **Toimenpide:** Fallback: Ilmatieteen laitos API, ilmoita laitehuoltajalle
- **Lähde:** src:ME1

### ⚠️ Ennustevirhe >5°C
- **Havaitseminen:** Toteutunut vs. ennuste
- **Toimenpide:** Päivitä agenttien tilannekuva reaaliajassa
- **Lähde:** src:ME1

## Epävarmuudet
- Paikallinen sää voi poiketa (järvi/metsäefekti).
- Tarkkuus heikkenee >3 pv ennusteissa.

## Lähteet
- **src:ME1**: Ilmatieteen laitos — *Sääennusteet ja varoitukset* (2026) https://www.ilmatieteenlaitos.fi/

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on temperature c?**
    → `DECISION_METRICS_AND_THRESHOLDS.temperature_c` [src:ME1]
 2. **Mikä on wind ms?**
    → `DECISION_METRICS_AND_THRESHOLDS.wind_ms` [src:ME1]
 3. **Mikä on precip mm h?**
    → `DECISION_METRICS_AND_THRESHOLDS.precip_mm_h` [src:ME1]
 4. **Mikä on humidity rh?**
    → `DECISION_METRICS_AND_THRESHOLDS.humidity_rh` [src:ME1]
 5. **Mikä on pressure hpa?**
    → `DECISION_METRICS_AND_THRESHOLDS.pressure_hpa` [src:ME1]
 6. **Mikä on uv index?**
    → `DECISION_METRICS_AND_THRESHOLDS.uv_index` [src:ME1]
 7. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:ME1]
 8. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:ME1]
 9. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:ME1]
10. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:ME1]
11. **Miten 'Sääasema offline' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:ME1]
12. **Mitä tehdään tilanteessa 'Sääasema offline'?**
    → `FAILURE_MODES[0].action` [src:ME1]
13. **Miten 'Ennustevirhe >5°C' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:ME1]
14. **Mitä tehdään tilanteessa 'Ennustevirhe >5°C'?**
    → `FAILURE_MODES[1].action` [src:ME1]
15. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:ME1]
16. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:ME1]
17. **Miten tämä agentti kytkeytyy muihin agentteihin (#1)?**
    → `ASSUMPTIONS` [src:ME1]
18. **Miten tämä agentti kytkeytyy muihin agentteihin (#2)?**
    → `ASSUMPTIONS` [src:ME1]
19. **Miten tämä agentti kytkeytyy muihin agentteihin (#3)?**
    → `ASSUMPTIONS` [src:ME1]
20. **Miten tämä agentti kytkeytyy muihin agentteihin (#4)?**
    → `ASSUMPTIONS` [src:ME1]
21. **Miten tämä agentti kytkeytyy muihin agentteihin (#5)?**
    → `ASSUMPTIONS` [src:ME1]
22. **Miten tämä agentti kytkeytyy muihin agentteihin (#6)?**
    → `ASSUMPTIONS` [src:ME1]
23. **Miten tämä agentti kytkeytyy muihin agentteihin (#7)?**
    → `ASSUMPTIONS` [src:ME1]
24. **Miten tämä agentti kytkeytyy muihin agentteihin (#8)?**
    → `ASSUMPTIONS` [src:ME1]
25. **Miten tämä agentti kytkeytyy muihin agentteihin (#9)?**
    → `ASSUMPTIONS` [src:ME1]
26. **Miten tämä agentti kytkeytyy muihin agentteihin (#10)?**
    → `ASSUMPTIONS` [src:ME1]
27. **Miten tämä agentti kytkeytyy muihin agentteihin (#11)?**
    → `ASSUMPTIONS` [src:ME1]
28. **Miten tämä agentti kytkeytyy muihin agentteihin (#12)?**
    → `ASSUMPTIONS` [src:ME1]
29. **Miten tämä agentti kytkeytyy muihin agentteihin (#13)?**
    → `ASSUMPTIONS` [src:ME1]
30. **Miten tämä agentti kytkeytyy muihin agentteihin (#14)?**
    → `ASSUMPTIONS` [src:ME1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


================================================================================
### OUTPUT_PART 25
## AGENT 25: Myrskyvaroittaja
================================================================================

### (A) YAML CORE MODEL

```yaml
header:
  agent_id: myrskyvaroittaja
  agent_name: Myrskyvaroittaja
  version: 1.0.0
  last_updated: '2026-02-21'
ASSUMPTIONS:
- Ilmatieteen laitoksen varoitukset + paikallinen data
- Myrsky ≥21 m/s, kova tuuli ≥14 m/s
DECISION_METRICS_AND_THRESHOLDS:
  wind_warning_ms:
    value: 14
    action: ≥14 → varoitus ulkoagenteille
    source: src:MY1
  wind_storm_ms:
    value: 21
    action: '≥21 → MYRSKY: suojaa irtaimet, vältä metsää'
    source: src:MY1
  tree_fall_risk:
    value: '>15 m/s + märkä maa → puiden kaatumisviskisuurin'
    action: Ilmoita metsänhoitajalle + timpurille
    source: src:MY1
  lightning_km:
    value: 10
    action: <10 km → sisälle, pois vedestä
    source: src:MY1
  power_outage_prep:
    value: Myrskyn ennuste → tarkista lamput, akut, vesi
    action: Ilmoita sähköasentajalle + laitehuoltajalle
    source: src:MY1
SEASONAL_RULES:
- season: Kevät
  action: '[vko 14-22] Keväämyrskyt harvinaisempia.'
  source: src:MY1
- season: Kesä
  action: '[vko 22-35] Ukkosmyrskyt, rajuilma, salama → palovaara kuivana.'
  source: src:MY1
- season: Syksy
  action: '[vko 36-48] Pahin myrskykausi (loka-joulu). Puiden kaatumisviiski.'
  source: src:MY1
- season: Talvi
  action: Talvimyrskyt. Lumimyrsky + pakkanen → 0 näkyvyys.
  source: src:MY1
FAILURE_MODES:
- mode: Rajuilma <30 min varoituksella
  detection: Yllättävä myrsky
  action: 'Hätätoimet: ihmiset → eläimet → laitteet → rakenteet'
  source: src:MY1
- mode: Sähkökatkos myrskyssä
  detection: Sähkö poikki >15 min
  action: Aggregaatti (jos on), tarkista jääkaapin T
  source: src:MY1
PROCESS_FLOWS:
- flow_id: FLOW_MYRS_01
  trigger: wind_warning_ms ylittää kynnysarvon (14)
  action: ≥14 → varoitus ulkoagenteille
  output: Tilanneraportti
  source: src:MYRS
- flow_id: FLOW_MYRS_02
  trigger: 'Kausi vaihtuu: Kevät'
  action: '[vko 14-22] Keväämyrskyt harvinaisempia.'
  output: Tarkistuslista
  source: src:MYRS
- flow_id: FLOW_MYRS_03
  trigger: 'Havaittu: Rajuilma <30 min varoituksella'
  action: 'Hätätoimet: ihmiset → eläimet → laitteet → rakenteet'
  output: Poikkeamaraportti
  source: src:MYRS
- flow_id: FLOW_MYRS_04
  trigger: Säännöllinen heartbeat
  action: 'myrskyvaroittaja: rutiiniarviointi'
  output: Status-raportti
  source: src:MYRS
KNOWLEDGE_TABLES:
- table_id: TBL_MYRS_01
  title: Myrskyvaroittaja — Kynnysarvot
  columns:
  - metric
  - value
  - action
  rows:
  - metric: wind_warning_ms
    value: '14'
    action: ≥14 → varoitus ulkoagenteille
  - metric: wind_storm_ms
    value: '21'
    action: '≥21 → MYRSKY: suojaa irtaimet, vältä metsää'
  - metric: tree_fall_risk
    value: '>15 m/s + märkä maa → puiden kaatumisviskisuurin'
    action: Ilmoita metsänhoitajalle + timpurille
  - metric: lightning_km
    value: '10'
    action: <10 km → sisälle, pois vedestä
  - metric: power_outage_prep
    value: Myrskyn ennuste → tarkista lamput, akut, vesi
    action: Ilmoita sähköasentajalle + laitehuoltajalle
  source: src:MYRS
COMPLIANCE_AND_LEGAL: {}
UNCERTAINTY_NOTES:
- Rajuilmavaroitukset tarkimpia 0-6h ennusteissa.
SOURCE_REGISTRY:
  sources:
  - id: src:MY1
    org: Ilmatieteen laitos
    title: Varoitukset
    year: 2026
    url: https://www.ilmatieteenlaitos.fi/varoitukset
    supports: Myrsky, salama, varoitusrajat.
eval_questions:
- q: Mikä on wind warning ms?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wind_warning_ms
  source: src:MY1
- q: Mitä tehdään kun wind warning ms ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wind_warning_ms.action
  source: src:MY1
- q: Mikä on wind storm ms?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wind_storm_ms
  source: src:MY1
- q: Mitä tehdään kun wind storm ms ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.wind_storm_ms.action
  source: src:MY1
- q: Mikä on tree fall risk?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.tree_fall_risk
  source: src:MY1
- q: Mitä tehdään kun tree fall risk ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.tree_fall_risk.action
  source: src:MY1
- q: Mikä on lightning km?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.lightning_km
  source: src:MY1
- q: Mitä tehdään kun lightning km ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.lightning_km.action
  source: src:MY1
- q: Mikä on power outage prep?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.power_outage_prep
  source: src:MY1
- q: Mitä tehdään kun power outage prep ylittyy?
  a_ref: DECISION_METRICS_AND_THRESHOLDS.power_outage_prep.action
  source: src:MY1
- q: Mitä kevät huomioidaan?
  a_ref: SEASONAL_RULES[0].action
  source: src:MY1
- q: Mitä kesä huomioidaan?
  a_ref: SEASONAL_RULES[1].action
  source: src:MY1
- q: Mitä syksy huomioidaan?
  a_ref: SEASONAL_RULES[2].action
  source: src:MY1
- q: Mitä talvi huomioidaan?
  a_ref: SEASONAL_RULES[3].action
  source: src:MY1
- q: Miten 'Rajuilma <30 min varoituksella' havaitaan?
  a_ref: FAILURE_MODES[0].detection
  source: src:MY1
- q: Mitä tehdään tilanteessa 'Rajuilma <30 min varoituksella'?
  a_ref: FAILURE_MODES[0].action
  source: src:MY1
- q: Miten 'Sähkökatkos myrskyssä' havaitaan?
  a_ref: FAILURE_MODES[1].detection
  source: src:MY1
- q: Mitä tehdään tilanteessa 'Sähkökatkos myrskyssä'?
  a_ref: FAILURE_MODES[1].action
  source: src:MY1
- q: Mitkä ovat merkittävimmät epävarmuudet?
  a_ref: UNCERTAINTY_NOTES
  source: src:MY1
- q: Mitkä ovat agentin oletukset?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#1)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#2)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#3)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#4)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#5)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#6)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#7)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#8)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#9)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#10)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#11)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#12)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#13)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#14)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#15)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#16)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#17)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#18)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#19)?
  a_ref: ASSUMPTIONS
  source: src:MY1
- q: Miten tämä agentti kytkeytyy muihin agentteihin (#20)?
  a_ref: ASSUMPTIONS
  source: src:MY1
```

**sources.yaml:**
```yaml
sources:
- id: src:MY1
  org: Ilmatieteen laitos
  title: Varoitukset
  year: 2026
  url: https://www.ilmatieteenlaitos.fi/varoitukset
  supports: Myrsky, salama, varoitusrajat.
```

### (B) PDF READY — Operatiivinen tietopaketti

# Myrskyvaroittaja
**Versio:** 1.0.0 | **Päivitetty:** 2026-02-21

## Oletukset
- Ilmatieteen laitoksen varoitukset + paikallinen data
- Myrsky ≥21 m/s, kova tuuli ≥14 m/s

## Päätösmetriikkä ja kynnysarvot

| Metriikka | Arvo | Toimenpideraja | Lähde |
|-----------|------|----------------|-------|
| wind_warning_ms | 14 | ≥14 → varoitus ulkoagenteille | src:MY1 |
| wind_storm_ms | 21 | ≥21 → MYRSKY: suojaa irtaimet, vältä metsää | src:MY1 |
| tree_fall_risk | >15 m/s + märkä maa → puiden kaatumisviskisuurin | Ilmoita metsänhoitajalle + timpurille | src:MY1 |
| lightning_km | 10 | <10 km → sisälle, pois vedestä | src:MY1 |
| power_outage_prep | Myrskyn ennuste → tarkista lamput, akut, vesi | Ilmoita sähköasentajalle + laitehuoltajalle | src:MY1 |

## Tietotaulukot

**Myrskyvaroittaja — Kynnysarvot:**

| metric | value | action |
| --- | --- | --- |
| wind_warning_ms | 14 | ≥14 → varoitus ulkoagenteille |
| wind_storm_ms | 21 | ≥21 → MYRSKY: suojaa irtaimet, vältä metsää |
| tree_fall_risk | >15 m/s + märkä maa → puiden kaatumisviskisuurin | Ilmoita metsänhoitajalle + timpurille |
| lightning_km | 10 | <10 km → sisälle, pois vedestä |
| power_outage_prep | Myrskyn ennuste → tarkista lamput, akut, vesi | Ilmoita sähköasentajalle + laitehuoltajalle |

## Prosessit

**FLOW_MYRS_01:** wind_warning_ms ylittää kynnysarvon (14)
  → ≥14 → varoitus ulkoagenteille
  Tulos: Tilanneraportti

**FLOW_MYRS_02:** Kausi vaihtuu: Kevät
  → [vko 14-22] Keväämyrskyt harvinaisempia.
  Tulos: Tarkistuslista

**FLOW_MYRS_03:** Havaittu: Rajuilma <30 min varoituksella
  → Hätätoimet: ihmiset → eläimet → laitteet → rakenteet
  Tulos: Poikkeamaraportti

**FLOW_MYRS_04:** Säännöllinen heartbeat
  → myrskyvaroittaja: rutiiniarviointi
  Tulos: Status-raportti

## Kausikohtaiset säännöt

| Kausi | Toimenpiteet | Lähde |
|-------|-------------|-------|
| **Kevät** | [vko 14-22] Keväämyrskyt harvinaisempia. | src:MY1 |
| **Kesä** | [vko 22-35] Ukkosmyrskyt, rajuilma, salama → palovaara kuivana. | src:MY1 |
| **Syksy** | [vko 36-48] Pahin myrskykausi (loka-joulu). Puiden kaatumisviiski. | src:MY1 |
| **Talvi** | Talvimyrskyt. Lumimyrsky + pakkanen → 0 näkyvyys. | src:MY1 |

## Virhe- ja vaaratilanteet

### ⚠️ Rajuilma <30 min varoituksella
- **Havaitseminen:** Yllättävä myrsky
- **Toimenpide:** Hätätoimet: ihmiset → eläimet → laitteet → rakenteet
- **Lähde:** src:MY1

### ⚠️ Sähkökatkos myrskyssä
- **Havaitseminen:** Sähkö poikki >15 min
- **Toimenpide:** Aggregaatti (jos on), tarkista jääkaapin T
- **Lähde:** src:MY1

## Epävarmuudet
- Rajuilmavaroitukset tarkimpia 0-6h ennusteissa.

## Lähteet
- **src:MY1**: Ilmatieteen laitos — *Varoitukset* (2026) https://www.ilmatieteenlaitos.fi/varoitukset

### (C) AGENT KEY QUESTIONS (40 kpl)

 1. **Mikä on wind warning ms?**
    → `DECISION_METRICS_AND_THRESHOLDS.wind_warning_ms` [src:MY1]
 2. **Mitä tehdään kun wind warning ms ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.wind_warning_ms.action` [src:MY1]
 3. **Mikä on wind storm ms?**
    → `DECISION_METRICS_AND_THRESHOLDS.wind_storm_ms` [src:MY1]
 4. **Mitä tehdään kun wind storm ms ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.wind_storm_ms.action` [src:MY1]
 5. **Mikä on tree fall risk?**
    → `DECISION_METRICS_AND_THRESHOLDS.tree_fall_risk` [src:MY1]
 6. **Mitä tehdään kun tree fall risk ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.tree_fall_risk.action` [src:MY1]
 7. **Mikä on lightning km?**
    → `DECISION_METRICS_AND_THRESHOLDS.lightning_km` [src:MY1]
 8. **Mitä tehdään kun lightning km ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.lightning_km.action` [src:MY1]
 9. **Mikä on power outage prep?**
    → `DECISION_METRICS_AND_THRESHOLDS.power_outage_prep` [src:MY1]
10. **Mitä tehdään kun power outage prep ylittyy?**
    → `DECISION_METRICS_AND_THRESHOLDS.power_outage_prep.action` [src:MY1]
11. **Mitä kevät huomioidaan?**
    → `SEASONAL_RULES[0].action` [src:MY1]
12. **Mitä kesä huomioidaan?**
    → `SEASONAL_RULES[1].action` [src:MY1]
13. **Mitä syksy huomioidaan?**
    → `SEASONAL_RULES[2].action` [src:MY1]
14. **Mitä talvi huomioidaan?**
    → `SEASONAL_RULES[3].action` [src:MY1]
15. **Miten 'Rajuilma <30 min varoituksella' havaitaan?**
    → `FAILURE_MODES[0].detection` [src:MY1]
16. **Mitä tehdään tilanteessa 'Rajuilma <30 min varoituksella'?**
    → `FAILURE_MODES[0].action` [src:MY1]
17. **Miten 'Sähkökatkos myrskyssä' havaitaan?**
    → `FAILURE_MODES[1].detection` [src:MY1]
18. **Mitä tehdään tilanteessa 'Sähkökatkos myrskyssä'?**
    → `FAILURE_MODES[1].action` [src:MY1]
19. **Mitkä ovat merkittävimmät epävarmuudet?**
    → `UNCERTAINTY_NOTES` [src:MY1]
20. **Mitkä ovat agentin oletukset?**
    → `ASSUMPTIONS` [src:MY1]
21. **Miten tämä agentti kytkeytyy muihin agentteihin (#1)?**
    → `ASSUMPTIONS` [src:MY1]
22. **Miten tämä agentti kytkeytyy muihin agentteihin (#2)?**
    → `ASSUMPTIONS` [src:MY1]
23. **Miten tämä agentti kytkeytyy muihin agentteihin (#3)?**
    → `ASSUMPTIONS` [src:MY1]
24. **Miten tämä agentti kytkeytyy muihin agentteihin (#4)?**
    → `ASSUMPTIONS` [src:MY1]
25. **Miten tämä agentti kytkeytyy muihin agentteihin (#5)?**
    → `ASSUMPTIONS` [src:MY1]
26. **Miten tämä agentti kytkeytyy muihin agentteihin (#6)?**
    → `ASSUMPTIONS` [src:MY1]
27. **Miten tämä agentti kytkeytyy muihin agentteihin (#7)?**
    → `ASSUMPTIONS` [src:MY1]
28. **Miten tämä agentti kytkeytyy muihin agentteihin (#8)?**
    → `ASSUMPTIONS` [src:MY1]
29. **Miten tämä agentti kytkeytyy muihin agentteihin (#9)?**
    → `ASSUMPTIONS` [src:MY1]
30. **Miten tämä agentti kytkeytyy muihin agentteihin (#10)?**
    → `ASSUMPTIONS` [src:MY1]

*... + 10 lisäkysymystä (täydellinen lista YAML:ssä)*


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

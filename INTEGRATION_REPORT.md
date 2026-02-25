# OpenClaw v1.4 — Runtime × Tietopohja Yhteensopivuusraportti

## Tilanne

Runtime-moottori (whisper_protocol.zip, 3551 riviä, 10 tiedostoa) on rakennettu **7 hardkoodatulle agentille**. Tietopohja (openclaw_v14_FINAL.zip) sisältää **50 validoitua YAML-agenttia** joiden päätösmetriiikat, vuosikellot ja vikatilat ovat valmiita. Nämä kaksi järjestelmää eivät tällä hetkellä puhu toisilleen.

## Kriittiset yhteensopivuusongelmat

### 1. Spawner: 7 → 50 agenttia
**spawner.py** sisältää `DEFAULT_TEMPLATES`-dictin jossa on 7 agenttia (beekeeper, video_producer, property, tech, business, hacker, oracle) kovakoodatuilla system_prompteilla. YAML-pohja sisältää 50 agenttia strukturoidulla tiedolla (metriikat, kynnysarvot, vuosikello, vikatilat).

**Ratkaisu**: `YAMLBridge.get_spawner_templates()` generoi spawner-yhteensopivat templateit suoraan YAML:stä. System_prompt rakennetaan automaattisesti koostamalla ASSUMPTIONS + DECISION_METRICS + SEASONAL_RULES + FAILURE_MODES.

### 2. Reititys: 7 avainsanaryhmää → 50
**hivemind.py** `chat()` sisältää kovakoodatun `routing_rules`-dictin jossa 7 avainsanaryhmää. 43 YAML-agentille ei ole reititystä.

**Ratkaisu**: `YAMLBridge.get_routing_rules()` palauttaa 50 agentin avainsanakartan. Dynaaminen scoring: viestin avainsanat matchataan kaikkia 50 agenttia vastaan, paras score voittaa.

### 3. Whisper-glyyfit: 8 → 53
**whisper_protocol.py** `AGENT_GLYPHS` sisältää 8 emoji-symbolia (beekeeper→🐝 jne). YAML-agenteille ei ole glyyfiä → kuiskaukset näyttävät "❓".

**Ratkaisu**: Laajennettu AGENT_GLYPHS 53 agentin karttaan. Jokaisella YAML-agentilla oma kategoriakohtainen emoji.

### 4. Knowledge Loader: ei lue YAML:ää
**knowledge_loader.py** lukee PDF, TXT, MD, CSV, JSON — mutta ei YAML. Agentin core.yaml (strukturoitu tietopankki) jää hyödyntämättä.

**Ratkaisu**: Lisätty `.yaml`/`.yml`-tuki `_read_file()`-metodiin. Uusi `_read_yaml()` parsii DECISION_METRICS, SEASONAL_RULES ja FAILURE_MODES luettavaan muotoon.

### 5. System Prompt: string vs. strukturoitu YAML
**base_agent.py** odottaa `system_prompt` parametriksi pelkkää stringiä. YAML-agenteilla tieto on strukturoidussa muodossa (dict/list).

**Ratkaisu**: `YAMLBridge.build_system_prompt()` koostaa YAML-datasta rikkaan system_promptin:
```
Olet Tarhaaja (Päämehiläishoitaja) — Mehiläispesien terveyden, kasvun ja tuotannon asiantuntija.

## PÄÄTÖSMETRIIKAT JA KYNNYSARVOT
- varroa_threshold_per_100: 3 → TOIMENPIDE: >3 punkkia/100 → kemiallinen hoito välittömästi
- colony_weight_spring_min_kg: 15 → TOIMENPIDE: Alle 15 kg → hätäruokinta sokeriliuoksella
...

## VUOSIKELLO
- Kevät: Pesien avaus huhti-toukokuussa, kun T>12°C...
- Kesä: Mehiläisten pääsatokausi. Lisää korotuksia...
```

## Puuttuvat tiedostot (ei mukana whisper_protocol.zip:ssä)

Nämä tarvitaan runtime-moottoriin mutta eivät ole mukana zip:ssä:

| Tiedosto | Rooli | Tarvitaanko? |
|---|---|---|
| `core/llm_provider.py` | LLMProvider, LLMResponse | Kyllä — Ollama/API-yhteys |
| `core/token_economy.py` | TokenEconomy, ORACLE_PRICES | Kyllä — tokeni-talous |
| `core/live_monitor.py` | LiveMonitor, MonitorEvent | Kyllä — reaaliaikainen feed |
| `memory/shared_memory.py` | SharedMemory (aiosqlite) | Kyllä — jaettu muisti |
| `web/dashboard.py` | FastAPI dashboard | Kyllä — web-UI |
| `configs/settings.yaml` | Asetukset | Kyllä — konfiguraatio |
| `main.py` | Käynnistin | Kyllä — entry point |

**Nämä ovat todennäköisesti olemassa OpenClaw-projektissa (S:\Python\openclaw\) mutta eivät olleet mukana zip:ssä.**

## Autonomia-analyysi

### ✅ Toimii nyt (runtime)
- **Heartbeat-looppi**: Agentit ajattelevat proaktiivisesti (2.5 min välein)
- **HiveMind-synteesi**: Oivallukset tiivistetään (5 min)
- **Whisper-kuiskaukset**: Kruunatut agentit jakavat viisautta (7.5 min)
- **Oracle-konsultaatio**: Kysymykset kerätään Claudelle (10 min)
- **Oracle-verkkohaku**: DuckDuckGo-tutkimus automaattisesti (15 min)
- **Token Economy**: Työ → tokenit → kruunu → kuiskaus
- **HackerAgent**: Koodin analysointi, korjaus, refaktorointi
- **Shared Memory**: Kaikki agentit jakavat muistin (aiosqlite)
- **Reflektio**: Agentit arvioivat omaa suoritustaan

### ❌ Puuttuu / rajallinen
- **50 agenttia**: Runtime tukee vain 7 → YAML Bridge korjaa
- **Dynaaminen reititys**: Hardkoodattu → YAML Bridge korjaa
- **YAML-tietopankki**: Ei lue strukturoitua dataa → Knowledge Loader patch korjaa
- **Finetuning**: JSONL generoitu mutta ei vielä käytetä Ollama-finetunessa

### 🔮 Seuraavat askeleet
1. **Ollama-finetune**: Käytä 1500 JSONL-paria mallin hienosäätöön
2. **Agentti-priorisointi**: Heartbeat voisi painottaa agentteja tarpeen mukaan
3. **Muisti-rankingit**: Oivallukset TF-IDF tai embedding-pohjainen haku
4. **Auto-scaling**: Spawna agentteja vain tarpeen mukaan (ei kaikkia 50 kerralla)

## Asennus

```bash
# 1. Kopioi yaml_bridge.py OpenClaw-projektiin
cp yaml_bridge.py S:/Python/openclaw/core/yaml_bridge.py

# 2. Kopioi agents/ YAML-kansiot
cp -r agents/ S:/Python/openclaw/agents/

# 3. Aja integraatiopatcher
cd S:/Python/openclaw
python integrate_yaml_agents.py

# 4. Käynnistä uudelleen
python main.py
```

## Testatut toiminnallisuudet

| Testi | Tulos |
|---|---|
| YAML-lataus (50 agenttia) | ✅ 50/50, 283 metriiikkaa, 2000 kysymystä |
| System prompt -generointi | ✅ 1300-2700 chars/agentti |
| Spawner-template generointi | ✅ 50 templateia |
| Dynaaminen reititys | ✅ "Pesien varroa?" → 🐝 tarhaaja |
| Glyyfikartta | ✅ 53 emojia |
| Knowledge summary | ✅ Metriikat + kausi-info kontekstissa |
| Kausi-detektio | ✅ Talvi (helmikuu) → oikea vuosikello-osio |

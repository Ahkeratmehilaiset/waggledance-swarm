# Swarm Scheduler — Agentti-integraatio-ohje

## v0.0.2 • 2026-02-22

Tämä dokumentti kuvaa miten agenttikansiot (`agents/`) ja YAML-agentit (`knowledge/`)
vuorovaikuttavat Swarm Schedulerin kanssa.

---

## 1. YAML-agentin minimirakenne schedulerille

Scheduler EI vaadi muutoksia olemassaoleviin YAML-tiedostoihin.
Se lukee automaattisesti seuraavat kentät:

```yaml
# knowledge/<agent_type>/core.yaml (tai agents/<agent_type>/core.yaml)
header:
  agent_name: "Tarhaaja"
  role: "Mehiläishoitaja"
  description: "Yhdyskuntien hoito ja tarkkailu"
  # VALINNAINEN: lisätags schedulerille
  keywords: ["mehiläi", "pesä", "varroa"]   # ← scheduler käyttää tageina

DECISION_METRICS_AND_THRESHOLDS:
  varroa_count:
    value: ">3 pudonneita/vrk"
    action: "Muurahaishappo-käsittely"
  honey_yield:
    value: "30-45 kg/yhdyskunta"
    action: "Linkous kun kehä 80% sinetöity"
  # → Näiden AVAIMET (varroa_count, honey_yield) muuntuvat skills-listaksi
```

### Minimikentät joita scheduler käyttää:

| Kenttä | Lähde | Käyttö |
|--------|-------|--------|
| `agent_type` | kansion nimi | Roolimappaus (scout/worker/judge) |
| `DECISION_METRICS` keys | YAML | → `skills` lista (max 8) |
| `header.keywords` | YAML | → `tags` (routing-avainsanat) |
| ROUTING_KEYWORDS | `yaml_bridge.py` | → `tags` (primary source) |

**Jos YAML:ssä ei ole `header.keywords`**, scheduler käyttää `yaml_bridge.py`:n
`ROUTING_KEYWORDS`-sanakirjaa, jossa kaikki 50 agenttia ovat jo valmiiksi.

---

## 2. Agentin rekisteröinti schedulerille

### A. Automaattinen (suositeltu)

Agentit rekisteröidään schedulerille **kolmella tavalla**, prioriteettijärjestyksessä:

1. **Spawn-hookki** (`hivemind.register_agent_to_scheduler(agent)`):
   - Kutsutaan automaattisesti kun agentti spawnataan `main.py`:ssä
   - Lukee agentin `skills`, `tags` ja `agent_type`
   - Tags luetaan YAMLBridgen routing-säännöistä

2. **Bulk-register** (`scheduler.register_from_yaml_bridge(yaml_bridge, spawner)`):
   - Kutsutaan kerran startupissa `main.py`:ssä
   - Rekisteröi KAIKKI YAMLBridgestä tunnetut agentit (myös ne joita ei ole spawnattu)
   - Scheduler oppii niiden tags/skills/roles etukäteen

3. **Spawner-hookki** (tulevaisuus):
   - Lisää `spawner.py`:hin `on_agent_spawned(callback)` -mekanismi
   - Callback kutsuu `hivemind.register_agent_to_scheduler(agent)`

### B. Manuaalinen

```python
# Suoraan schedulerin API:n kautta
hive.scheduler.register_agent(
    agent_id="tarhaaja_001",
    agent_type="tarhaaja",
    skills=["varroa_count", "honey_yield"],
    tags=["mehiläi", "pesä", "hunaj", "varroa"],
)
```

### C. Idempotent

`register_agent()` on turvallinen kutsua useamman kerran samalle agentille.
Toinen kutsu vain päivittää tags/skills.

---

## 3. Spawn-prosessin vaiheet

```
1. spawner.spawn("tarhaaja")
   ├── Luo Agent-instanssi (base_agent.py)
   ├── Lataa system_prompt (YAMLBridge tai template)
   └── Palauttaa agent-objektin

2. hivemind.register_agent_to_scheduler(agent)
   ├── Lukee agent.id, agent.agent_type, agent.skills
   ├── Hakee tags YAMLBridgen routing_rules:sta
   └── Kutsuu scheduler.register_agent(...)

3. scheduler.register_agent(...)
   ├── Määrittelee roolin: DEFAULT_ROLE_MAP[agent_type] → scout/worker/judge
   ├── Luo AgentScore (pheromone-alkuarvot 0.5)
   ├── Laskee confidence_prior skills-listasta
   └── Tallentaa tags → AgentScore.tags (Top-K matchissa)
```

---

## 4. Chat-reitityksen pipeline

```
Käyttäjä: "Kuinka monta varroapunkkia on normaali?"
                    │
                    ▼
          ┌─ swarm.enabled? ─┐
          │ true              │ false
          ▼                   ▼
    _swarm_route()      _legacy_route()
          │                   │
    (A) Task meta:            │
    tags = ["varroa",         │
     "punkkia", "normaali"]   │
          │                   │
    (B) Top-K shortlist       │
    scheduler →               │
    [tarhaaja, tautivahti,    │
     pesalampo, ...]          │
    (6-12 agenttia)           │
          │                   │
    (C) Keyword-score         ├── Keyword-score
    VAIN shortlistatuille     │   KAIKILLE 50 agentille
          │                   │
    (D) Voittaja:             │
    tarhaaja (score=10)       │
          │                   │
    ┌─ Jos tyhjä → ──────────┘
    │  fallback _legacy_route()
    ▼
    _delegate_to_agent("tarhaaja", ...)
    ├── _enriched_prompt (päivämäärä + tietopankki)
    ├── agent.think(message, context)
    ├── prompt PALAUTETAAN alkuperäiseen ← FIX-1
    └── scheduler.record_task_result(...)
```

---

## 5. Usage/load/score-tilastot

### Muistissa (AgentScore dataclass):

| Kenttä | Tyyppi | Kuvaus |
|--------|--------|--------|
| success_score | float 0..1 | EMA onnistumisprosentti |
| speed_score | float 0..1 | EMA nopeus (latenssi) |
| reliability_score | float 0..1 | EMA luotettavuus |
| active_tasks | int | Juuri nyt käynnissä olevat |
| tasks_last_10min | int | 10 min laskuri |
| total_tasks_today | int | Päivän tehtävät |
| consecutive_wins | int | Peräkkäiset voitot (cooldown) |

### Persistenssi (SQLite):

Tällä hetkellä tilastot ovat **vain muistissa** ja nollautuvat uudelleenkäynnistyksessä.

Tulevaisuudessa (`v0.0.3`): Persistoidaan SQLiteen:

```sql
CREATE TABLE IF NOT EXISTS swarm_scores (
    agent_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    role TEXT DEFAULT 'worker',
    success_score REAL DEFAULT 0.5,
    speed_score REAL DEFAULT 0.5,
    reliability_score REAL DEFAULT 0.5,
    total_tasks INTEGER DEFAULT 0,
    calibrated BOOLEAN DEFAULT 0,
    calibration_score REAL DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    task_type TEXT,
    success BOOLEAN,
    latency_ms REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 6. Feature flag

```yaml
# configs/settings.yaml
swarm:
  enabled: true   # true = Swarm routing, false = legacy keyword routing
```

Kun `enabled: false`:
- Scheduler luodaan mutta sitä EI käytetä reitityksessä
- Kaikki 50 agenttia evaluoidaan keyword-scorella (vanha käytös)
- Tilastot kerätään edelleen (record_task_result toimii)
- Dashboard näyttää swarm stats mutta merkittynä "DISABLED"

---

## 7. Muutokset agents/-kansioon

**Tarvittavat muutokset: EI MITÄÄN.**

Scheduler toimii 100% ilman muutoksia `agents/base_agent.py` tai `agents/spawner.py`:hin.

**Suositellut (valinnainen) muutokset tulevaisuudessa:**

1. `spawner.py`: Lisää `on_agent_spawned` callback:
   ```python
   # spawner.py
   class AgentSpawner:
       def __init__(self, ...):
           self._on_spawn_callbacks = []
       
       def on_agent_spawned(self, callback):
           self._on_spawn_callbacks.append(callback)
       
       async def spawn(self, agent_type):
           agent = ...  # nykyinen logiikka
           for cb in self._on_spawn_callbacks:
               cb(agent)
           return agent
   ```

2. `base_agent.py`: Lisää `tags` kenttä:
   ```python
   class Agent:
       def __init__(self, ..., tags=None):
           self.tags = tags or []
   ```

Nämä ovat **valinnaisia** parannuksia jotka tekevät integraaation vieläkin automaattisemmaksi.

---

## 8. Tiedostot joita tarvitsen jatkokehitykseen

Pienin mahdollinen "minipaketti" tarkempaa integraatiota varten:

| Tiedosto | Miksi | Prioriteetti |
|----------|-------|-------------|
| `agents/base_agent.py` | Agent-luokan rakenne, skills-kenttä, think()-signatuuri | ⭐⭐⭐ |
| `agents/spawner.py` | spawn()-logiikka, agent_templates, yaml_bridge-integraatio | ⭐⭐⭐ |
| `knowledge/tarhaaja/core.yaml` | Esimerkkiagentti YAML-rakenteen varmistamiseen | ⭐⭐ |
| 1-2 muuta knowledge/*.yaml | Eri YAML-varianttien testaus | ⭐ |

**Ei tarvita**: loput 47 YAML-agenttia (rakenne on sama), tools/, web/ (jo tiedossa).

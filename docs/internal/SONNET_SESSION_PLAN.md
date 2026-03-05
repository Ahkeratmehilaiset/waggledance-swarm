# SONNET 8H SESSION — Keskeytymätön toteutus

**Päivä:** 2026-03-04
**Tavoite:** 6 blokkia, 35 suitea lopussa, print-siivous, Phase 11 live
**Sääntö:** Tee kaikki blokit A→F järjestyksessä. Älä kysy vahvistuksia. Aja testit jokaisen blokin jälkeen.

---

## KRIITTISET SÄÄNNÖT (lue ensin CLAUDE.md, mutta nämä ovat tärkeimmät)

1. Kaikki LLM sisäisesti ENGLANNIKSI — suomi vain user I/O
2. phi4-mini VAIN chatille, llama3.2:1b KAIKKI tausta
3. nomic-embed-text: "search_document:"/"search_query:" prefixöt PAKOLLINEN
4. Chat AINA voittaa — PriorityLock pysäyttää taustan
5. UTF-8 kaikkialla — ä, ö, å
6. Windows 11 yhteensopivuus
7. Graceful degradation — puuttuva komponentti → log warning, jatka
8. Testaa jokainen blokki ennen seuraavaa
9. `TranslationResult.text` — ÄLÄ koskaan konkatenoi TranslationResult suoraan

---

## BLOKKI A: ElasticScaler wiring + testit (1.5h)

### A1: Wire ElasticScaler hivemind.py:hin

**Tiedosto:** `hivemind.py`

1. Lisää import riville ~35 (muiden core-importtien joukkoon):
```python
from core.elastic_scaler import ElasticScaler
```

2. Lisää `__init__()`:iin riville ~568 (Phase 7:n jälkeen):
```python
        # ── Phase 11: Elastic Scaling ────────────────────
        self.elastic_scaler = None
```

3. Lisää `start()`:iin riville ~997 (VoiceInterface-lohkon jälkeen, ennen cache warming):
```python
        # ── Phase 11: Elastic Scaling ──────────────────────────
        try:
            es_cfg = self.config.get("elastic_scaling", {})
            if es_cfg.get("enabled", True):
                self.elastic_scaler = ElasticScaler()
                tier = self.elastic_scaler.detect()
                log.info(f"ElasticScaler: tier={tier.tier}, "
                         f"chat={tier.chat_model}, bg={tier.bg_model}")
                print(f"  ✅ ElasticScaler: {tier.tier.upper()} tier", flush=True)
            else:
                print("  ℹ️  ElasticScaler DISABLED", flush=True)
        except Exception as e:
            print(f"  ⚠️  ElasticScaler: {e}", flush=True)
            self.elastic_scaler = None
```

4. Lisää `get_status()`:iin riville ~1994 (micro_model-kohdan jälkeen):
```python
            "elastic_scaler": (self.elastic_scaler.summary()
                               if hasattr(self, 'elastic_scaler')
                               and self.elastic_scaler else {}),
```

### A2: Luo tests/test_elastic_scaler.py

**Tiedosto:** `tests/test_elastic_scaler.py` (UUSI, ~200 riviä)

```python
#!/usr/bin/env python3
"""
WaggleDance — Elastic Scaler Tests (Phase 11)
15 tests across 4 groups:
  1. Syntax (1): elastic_scaler.py parses
  2. Dataclasses (4): HardwareProfile, TierConfig defaults + fields
  3. Tier logic (6): classify_tier for each tier level + edge cases
  4. Runtime (4): should_unload, should_spawn, summary keys, integration
"""
import ast
import os
import sys
import unittest

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestSyntax(unittest.TestCase):
    def test_elastic_scaler_syntax(self):
        fpath = os.path.join(_project_root, "core", "elastic_scaler.py")
        with open(fpath, encoding="utf-8") as f:
            ast.parse(f.read())


class TestDataclasses(unittest.TestCase):
    def setUp(self):
        from core.elastic_scaler import HardwareProfile, TierConfig, TIERS
        self.HardwareProfile = HardwareProfile
        self.TierConfig = TierConfig
        self.TIERS = TIERS

    def test_hardware_profile_defaults(self):
        hp = self.HardwareProfile()
        self.assertEqual(hp.cpu_cores, 0)
        self.assertEqual(hp.ram_gb, 0.0)
        self.assertEqual(hp.gpu_vram_gb, 0.0)
        self.assertEqual(hp.gpu_name, "")

    def test_tier_config_defaults(self):
        tc = self.TierConfig()
        self.assertEqual(tc.tier, "minimal")
        self.assertIsNone(tc.chat_model)
        self.assertEqual(tc.max_agents, 0)
        self.assertFalse(tc.vision)

    def test_tiers_dict_has_5_tiers(self):
        self.assertEqual(len(self.TIERS), 5)
        for name in ("minimal", "light", "standard", "professional", "enterprise"):
            self.assertIn(name, self.TIERS)

    def test_tier_required_keys(self):
        for name, spec in self.TIERS.items():
            self.assertIn("chat_model", spec, f"{name} missing chat_model")
            self.assertIn("bg_model", spec, f"{name} missing bg_model")
            self.assertIn("max_agents", spec, f"{name} missing max_agents")
            self.assertIn("min_vram_gb", spec, f"{name} missing min_vram_gb")
            self.assertIn("min_ram_gb", spec, f"{name} missing min_ram_gb")


class TestTierClassification(unittest.TestCase):
    def setUp(self):
        from core.elastic_scaler import ElasticScaler, HardwareProfile
        self.scaler = ElasticScaler()
        self.HardwareProfile = HardwareProfile

    def _classify(self, vram, ram):
        hw = self.HardwareProfile(gpu_vram_gb=vram, ram_gb=ram)
        return self.scaler._classify_tier(hw)

    def test_classify_minimal(self):
        t = self._classify(0, 4)
        self.assertEqual(t.tier, "minimal")

    def test_classify_light(self):
        t = self._classify(3, 16)
        self.assertEqual(t.tier, "light")

    def test_classify_standard(self):
        """ZBook: 8GB VRAM, 128GB RAM → standard"""
        t = self._classify(8, 128)
        self.assertEqual(t.tier, "standard")

    def test_classify_professional(self):
        t = self._classify(24, 64)
        self.assertEqual(t.tier, "professional")

    def test_classify_enterprise(self):
        t = self._classify(1536, 4096)
        self.assertEqual(t.tier, "enterprise")

    def test_classify_low_ram_demotes(self):
        """High VRAM but low RAM should demote tier"""
        t = self._classify(24, 8)
        # 24GB VRAM but only 8GB RAM → can't be professional (needs 32GB RAM)
        self.assertIn(t.tier, ("light", "standard"))


class TestRuntime(unittest.TestCase):
    def setUp(self):
        from core.elastic_scaler import ElasticScaler
        self.scaler = ElasticScaler()

    def test_should_unload_model_high(self):
        self.assertTrue(self.scaler.should_unload_model(95.0))

    def test_should_unload_model_low(self):
        self.assertFalse(self.scaler.should_unload_model(50.0))

    def test_should_spawn_agent(self):
        self.assertTrue(self.scaler.should_spawn_agent(15))
        self.assertFalse(self.scaler.should_spawn_agent(5))

    def test_summary_keys(self):
        s = self.scaler.summary()
        self.assertIn("tier", s)
        self.assertIn("chat_model", s)
        self.assertIn("hardware", s)
        self.assertIn("reason", s)
        self.assertIsInstance(s["hardware"], dict)
        self.assertIn("cpu", s["hardware"])
        self.assertIn("gpu", s["hardware"])


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Elastic Scaler — Test Suite")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
```

### A3: Rekisteröi suite #33

**Tiedosto:** `tools/waggle_backup.py`

Lisää rivi 113:n jälkeen (Phase 6 -kohdan jälkeen, Phase 7:n edelle):
```python
    # Phase 11 — Elastic Scaling
    {"file": "tests/test_elastic_scaler.py",     "name": "Elastic Scaler",           "phase": "11",   "args": [], "timeout": 30},
```

### A4: Validoi

```bash
python tests/test_elastic_scaler.py     # 15/15 PASS
python -c "from core.elastic_scaler import ElasticScaler; s=ElasticScaler(); print(s.summary())"
```

---

## BLOKKI B: Puuttuvat testit (2h)

### B1: Luo tests/test_training_collector.py

**Tiedosto:** `tests/test_training_collector.py` (UUSI, ~180 riviä)

Testaa `core/training_collector.py` TrainingDataCollector-luokkaa:

```python
#!/usr/bin/env python3
"""
WaggleDance — Training Data Collector Tests (Phase 10)
15 tests across 3 groups:
  1. Syntax (1): training_collector.py parses
  2. Core logic (10): init, thresholds, collect, reject, dedup, normalize, finetune parsing
  3. Integration (4): confidence per source, stats, edge cases
"""
import ast
import json
import os
import sys
import tempfile
import unittest

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestSyntax(unittest.TestCase):
    def test_training_collector_syntax(self):
        fpath = os.path.join(_project_root, "core", "training_collector.py")
        with open(fpath, encoding="utf-8") as f:
            ast.parse(f.read())


class TestCoreLogic(unittest.TestCase):
    def setUp(self):
        from core.training_collector import TrainingDataCollector
        self.collector = TrainingDataCollector(consciousness=None, data_dir="data")

    def test_init_defaults(self):
        self.assertEqual(self.collector._pairs, [])
        self.assertEqual(self.collector._total_collected, 0)
        self.assertEqual(self.collector._total_rejected, 0)

    def test_confidence_thresholds_defined(self):
        t = self.collector.CONFIDENCE_THRESHOLDS
        self.assertIn("round_table_consensus", t)
        self.assertIn("user_accepted", t)
        self.assertEqual(t["round_table_consensus"], 0.90)

    def test_min_training_confidence(self):
        self.assertEqual(self.collector.MIN_TRAINING_CONFIDENCE, 0.75)

    def test_collect_valid_pair(self):
        ok = self.collector.collect_training_pair(
            "Mikä on varroa?", "Varroa destructor on mehiläisten loinen joka aiheuttaa varroatautia.",
            "finetune_live", 0.85)
        self.assertTrue(ok)
        self.assertEqual(self.collector._total_collected, 1)

    def test_reject_low_confidence(self):
        ok = self.collector.collect_training_pair(
            "Testi?", "Vastaus tähän kysymykseen on tärkeä.",
            "web", 0.30)
        self.assertFalse(ok)
        self.assertEqual(self.collector._total_rejected, 1)

    def test_reject_short_answer(self):
        ok = self.collector.collect_training_pair(
            "Mikä on hunaja?", "Hunaja.",
            "web", 0.90)
        self.assertFalse(ok)

    def test_reject_empty_question(self):
        ok = self.collector.collect_training_pair(
            "", "Pitkä vastaus tähän tyhjään kysymykseen.",
            "web", 0.90)
        self.assertFalse(ok)

    def test_dedup_by_question(self):
        self.collector.collect_training_pair(
            "Mikä on varroa?", "Varroa on loinen joka vaikuttaa mehiläisiin merkittävästi.",
            "finetune_live", 0.85)
        ok = self.collector.collect_training_pair(
            "mikä on varroa?", "Toinen vastaus varroasta ja sen vaikutuksista pesiin.",
            "finetune_live", 0.85)
        self.assertFalse(ok)  # dedup

    def test_normalize_question(self):
        n = self.collector._normalize_question("  Mikä ON varroa?!  ")
        self.assertEqual(n, "mikä on varroa")

    def test_collect_from_finetune_live_missing_file(self):
        """Should handle missing file gracefully."""
        collector = type(self.collector)(consciousness=None, data_dir="/nonexistent")
        count = collector.collect_from_finetune_live()
        self.assertEqual(count, 0)


class TestIntegration(unittest.TestCase):
    def setUp(self):
        from core.training_collector import TrainingDataCollector
        self.collector = TrainingDataCollector(consciousness=None, data_dir="data")

    def test_collect_from_finetune_live_with_data(self):
        """Parse real finetune_live.jsonl if it exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a mock finetune_live.jsonl
            path = os.path.join(tmpdir, "finetune_live.jsonl")
            entry = {
                "messages": [
                    {"role": "system", "content": "You are a bee expert"},
                    {"role": "user", "content": "Milloin varroa käsitellään?"},
                    {"role": "assistant", "content": "Varroa käsitellään tyypillisesti kesän jälkeen elokuussa oksaalihapolla tai muurahaishapolla. Käsittely on kriittinen talvehtimisen onnistumiselle."}
                ],
                "agent": "beekeeper",
                "timestamp": "2026-03-04T10:00:00"
            }
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            from core.training_collector import TrainingDataCollector
            collector = TrainingDataCollector(consciousness=None, data_dir=tmpdir)
            count = collector.collect_from_finetune_live()
            self.assertEqual(count, 1)

    def test_reject_error_responses(self):
        """Error responses in finetune data should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "finetune_live.jsonl")
            entry = {
                "messages": [
                    {"role": "system", "content": "Expert"},
                    {"role": "user", "content": "Mikä on hunaja?"},
                    {"role": "assistant", "content": "Error: timeout occurred while processing this request"}
                ]
            }
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            from core.training_collector import TrainingDataCollector
            collector = TrainingDataCollector(consciousness=None, data_dir=tmpdir)
            count = collector.collect_from_finetune_live()
            self.assertEqual(count, 0)

    def test_stats_property_or_method(self):
        """Collector should expose stats somehow."""
        self.collector.collect_training_pair(
            "Testikysymys mehiläisistä?", "Pitkä ja yksityiskohtainen vastaus mehiläisistä ja pesistä.",
            "test", 0.80)
        self.assertEqual(self.collector._total_collected, 1)
        self.assertEqual(len(self.collector._pairs), 1)

    def test_pairs_structure(self):
        self.collector.collect_training_pair(
            "Mikä on propolis?", "Propolis on mehiläisten keräämä pihkamainen aine jota ne käyttävät pesän tiivistämiseen.",
            "user_accepted", 0.85)
        pair = self.collector._pairs[0]
        self.assertIn("question", pair)
        self.assertIn("answer", pair)
        self.assertIn("source", pair)
        self.assertIn("confidence", pair)
        self.assertIn("timestamp", pair)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Training Data Collector — Test Suite")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
```

Rekisteröi suite #34 `waggle_backup.py`:hin:
```python
    # Phase 10 — Training Data
    {"file": "tests/test_training_collector.py", "name": "Training Collector",      "phase": "10",   "args": [], "timeout": 30},
```

### B2: Luo tests/test_swarm_routing.py

**Tiedosto:** `tests/test_swarm_routing.py` (UUSI, ~200 riviä)

Testaa `core/swarm_scheduler.py` SwarmScheduler + AgentScore:

```python
#!/usr/bin/env python3
"""
WaggleDance — Swarm Routing Tests
12 tests across 3 groups:
  1. Syntax (1): swarm_scheduler.py parses
  2. AgentScore + Scheduler (7): init, register, roles, tags, select_candidates, exploration
  3. Integration (4): yaml_bridge bulk register, agent_count, DEFAULT_ROLE_MAP, pheromone
"""
import ast
import os
import sys
import unittest

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestSyntax(unittest.TestCase):
    def test_swarm_scheduler_syntax(self):
        fpath = os.path.join(_project_root, "core", "swarm_scheduler.py")
        with open(fpath, encoding="utf-8") as f:
            ast.parse(f.read())


class TestSchedulerCore(unittest.TestCase):
    def setUp(self):
        from core.swarm_scheduler import SwarmScheduler, AgentScore, AGENT_ROLES, DEFAULT_ROLE_MAP
        self.SwarmScheduler = SwarmScheduler
        self.AgentScore = AgentScore
        self.AGENT_ROLES = AGENT_ROLES
        self.DEFAULT_ROLE_MAP = DEFAULT_ROLE_MAP
        self.scheduler = SwarmScheduler({
            "top_k": 5,
            "exploration_rate": 0.2,
            "min_bids_per_day": 3,
        })

    def test_init_defaults(self):
        self.assertEqual(self.scheduler._top_k, 5)
        self.assertIsInstance(self.scheduler._agents, dict)

    def test_agent_score_defaults(self):
        a = self.AgentScore(agent_id="test_1", agent_type="beekeeper")
        self.assertEqual(a.agent_id, "test_1")
        self.assertEqual(a.agent_type, "beekeeper")
        self.assertEqual(a.role, "worker")
        self.assertIsInstance(a.tags, list)

    def test_register_agent(self):
        self.scheduler.register_agent("bee_1", "beekeeper", tags=["mehiläinen", "hunaja"])
        self.assertIn("bee_1", self.scheduler._agents)
        self.assertEqual(self.scheduler._agents["bee_1"].tags, ["mehiläinen", "hunaja"])

    def test_agent_roles_defined(self):
        self.assertIn("scout", self.AGENT_ROLES)
        self.assertIn("worker", self.AGENT_ROLES)
        self.assertIn("judge", self.AGENT_ROLES)

    def test_default_role_map_coverage(self):
        """Most agent types should have a default role."""
        self.assertIn("beekeeper", self.DEFAULT_ROLE_MAP)
        self.assertIn("meteorologist", self.DEFAULT_ROLE_MAP)
        self.assertGreater(len(self.DEFAULT_ROLE_MAP), 30)

    def test_agent_count_property(self):
        self.assertEqual(self.scheduler.agent_count, 0)
        self.scheduler.register_agent("a1", "beekeeper")
        self.assertEqual(self.scheduler.agent_count, 1)

    def test_select_candidates_empty(self):
        """No agents registered → empty candidates."""
        result = self.scheduler.select_candidates(["mehiläinen"])
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)


class TestIntegration(unittest.TestCase):
    def setUp(self):
        from core.swarm_scheduler import SwarmScheduler
        self.scheduler = SwarmScheduler({"top_k": 8, "exploration_rate": 0.1})

    def test_register_multiple_agents(self):
        for i in range(10):
            self.scheduler.register_agent(f"agent_{i}", "beekeeper")
        self.assertEqual(self.scheduler.agent_count, 10)

    def test_select_candidates_with_agents(self):
        self.scheduler.register_agent("bee_1", "beekeeper", tags=["hunaja", "mehiläinen"])
        self.scheduler.register_agent("met_1", "meteorologist", tags=["sää", "tuuli"])
        result = self.scheduler.select_candidates(["hunaja"])
        self.assertIsInstance(result, list)

    def test_pheromone_update(self):
        """Pheromone should be updatable."""
        self.scheduler.register_agent("bee_1", "beekeeper")
        agent = self.scheduler._agents["bee_1"]
        initial = agent.pheromone
        self.scheduler.record_success("bee_1")
        self.assertGreaterEqual(self.scheduler._agents["bee_1"].pheromone, initial)

    def test_record_failure(self):
        self.scheduler.register_agent("bee_1", "beekeeper")
        self.scheduler.record_failure("bee_1")
        # Should not crash on unknown agent either
        self.scheduler.record_failure("nonexistent_agent")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Swarm Routing — Test Suite")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
```

**HUOM:** Tarkista ensin `swarm_scheduler.py`:stä tarkat metodien nimet:
- `register_agent(agent_id, agent_type, tags=None)` — tarkista parametrit
- `select_candidates(tags)` — tarkista nimi ja parametrit
- `record_success(agent_id)` / `record_failure(agent_id)` — tarkista nimet
- `agent_count` property
- `pheromone` attribuutti AgentScoressa

Jos nimet poikkeavat, käytä oikeita nimiä. Lue `core/swarm_scheduler.py` kokonaan ennen testien kirjoitusta.

Rekisteröi suite #35 `waggle_backup.py`:hin:
```python
    # Swarm Routing
    {"file": "tests/test_swarm_routing.py",      "name": "Swarm Routing",            "phase": "swarm","args": [], "timeout": 30},
```

### B3: Lisää AudioMonitor-testit test_smart_home.py:hin

**Tiedosto:** `tests/test_smart_home.py`

Lisää uusi TestCase-luokka tiedoston loppuun (ennen `if __name__`):

```python
class TestAudioMonitorInSensorHub(unittest.TestCase):
    """AudioMonitor integration in SensorHub."""

    def test_sensor_hub_has_audio_monitor_attr(self):
        from integrations.sensor_hub import SensorHub
        hub = SensorHub(config={})
        self.assertTrue(hasattr(hub, "audio_monitor"))
        self.assertIsNone(hub.audio_monitor)

    def test_get_status_includes_audio(self):
        from integrations.sensor_hub import SensorHub
        hub = SensorHub(config={})
        status = hub.get_status()
        self.assertIn("audio_monitor", status)

    def test_sensor_context_handles_no_audio(self):
        from integrations.sensor_hub import SensorHub
        hub = SensorHub(config={})
        ctx = hub.get_sensor_context()
        self.assertIsInstance(ctx, str)

    def test_sensor_hub_stop_handles_no_audio(self):
        import asyncio
        from integrations.sensor_hub import SensorHub
        hub = SensorHub(config={})
        # stop() should not crash when audio_monitor is None
        asyncio.get_event_loop().run_until_complete(hub.stop())
```

### B4: Validoi blokki B

```bash
python tests/test_elastic_scaler.py        # 15/15
python tests/test_training_collector.py    # 15/15
python tests/test_swarm_routing.py         # 12/12
python tests/test_smart_home.py            # 30+4 = 34
```

---

## BLOKKI C: print() → log siivous (1.5h)

### C1: hivemind.py (74 print-kutsua)

**Tiedosto:** `hivemind.py`

Korvaa KAIKKI `print(...)` kutsut `log.*()` -kutsuilla seuraavasti:

**Patternsit:**
| print-tyyppi | Korvaus |
|---|---|
| `print("  ✅ ...")` | `log.info(...)` — poista emoji |
| `print("  ⚠️  ...")` | `log.warning(...)` |
| `print("  ℹ️  ...")` | `log.info(...)` |
| `print("  📍 ...")` | `log.info(...)` |
| `print("🐝 WaggleDance...")` | `log.info("WaggleDance Swarm AI starting...")` |
| `print("🟢 WaggleDance...")` | `log.info("WaggleDance Swarm AI running")` |
| `print("🔴 Sammutetaan...")` | `log.info("Shutting down WaggleDance...")` |
| `print(f"🌐 Kielitila: {mode}")` | `log.info(f"Language mode: {mode}")` |

**Tärkeää:**
- Poista `flush=True` — log ei tarvitse sitä
- Poista emojit log-viesteistä
- Säilytä informatiivinen viestisisältö
- Älä muuta rivien sisältöä muuten
- `log = logging.getLogger("hivemind")` on jo tiedoston alussa

**Esimerkki konversioita:**

```python
# ENNEN:
print("🐝 WaggleDance Swarm AI käynnistyy...", flush=True)
print("  ✅ Muisti alustettu", flush=True)
print(f"  ✅ LLM (chat): {self.llm.model} [GPU]", flush=True)
print(f"  ⚠️  Benchmark epäonnistui ({e}), käytetään oletuksia")
print("  ℹ️  Data Feeds DISABLED (feeds.enabled: false)", flush=True)
print("  WaggleDance sammutettu.")

# JÄLKEEN:
log.info("WaggleDance Swarm AI starting...")
log.info("Memory initialized")
log.info("LLM (chat): %s [GPU]", self.llm.model)
log.warning("Benchmark failed (%s), using defaults", e)
log.info("Data Feeds DISABLED (feeds.enabled: false)")
log.info("WaggleDance shutdown complete")
```

### C2: core/*.py (48 print-kutsua, 7 tiedostossa)

**Tiedostot ja print-määrät:**

| Tiedosto | print()-kpl | Huomio |
|---|---|---|
| `core/auto_install.py` | 20 | Nämä JÄTETÄÄN print():ksi — pyörii ennen loggingin alustusta |
| `core/adaptive_throttle.py` | 14 | → `log.info()` / `log.debug()` |
| `core/yaml_bridge.py` | 5 | → `log.info()` |
| `core/learning_engine.py` | 3 | → `log.info()` |
| `core/live_monitor.py` | 2 | → `log.info()` |
| `core/ops_agent.py` | 2 | → `log.info()` |
| `core/knowledge_loader.py` | 2 | → `log.info()` |

**POIKKEUS:** `core/auto_install.py` print-kutsut JÄTETÄÄN koskemattomiksi koska se ajetaan ennen kuin logging on konfiguroitu (pip install -vaihe).

**Tarkista** jokaisen tiedoston alussa, että `log = logging.getLogger("module_name")` on olemassa. Jos ei ole, lisää se importtien jälkeen.

### C3: Validoi

```bash
python -c "import hivemind; print('OK')"
python tests/test_phase6_audio.py   # Ei regressionia
```

---

## BLOKKI D: Dashboard päivitykset (1h)

### D1: App.jsx — Testien lukumäärä

**Tiedosto:** `dashboard/src/App.jsx`

Etsi ja korvaa KAIKKI "30/30" ja "30 test" viittaukset:

| Rivi | Vanha | Uusi |
|---|---|---|
| 178 | `all 30 test suites GREEN` | `all 35 test suites GREEN` |
| 190 | `30/30 test suites GREEN (700+ assertions)` | `35/35 test suites GREEN (700+ assertions)` |
| 196 | `30 test suites` | `35 test suites` |
| 472 | `kaikki 30 testisarjaa VIHREÄNÄ` | `kaikki 35 testisarjaa VIHREÄNÄ` |
| 484 | `30/30 testisarjaa VIHREÄNÄ (700+ väittämää)` | `35/35 testisarjaa VIHREÄNÄ (700+ väittämää)` |
| 490 | `30 testisarjaa` | `35 testisarjaa` |
| 698 | `30/30 test suites GREEN` (boot-animaatio) | `35/35 test suites GREEN` |
| 976 | `"Tests",v:"30/30"` (statusbar) | `"Tests",v:"35/35"` |

Käytä `replace_all` Editillä: korvaa `30/30` → `35/35` ja `30 test` → `35 test` ja `all 30 ` → `all 35 ` ja `kaikki 30 ` → `kaikki 35 `.

### D2: App.jsx — Tier statusbar-indikaattori

Rivi ~975, `aw`-taulukkoon (samaan kohtaan missä MQTT/HA/Cam/Audio/STT/TTS):

Lisää ElasticScaler tier `_ss`:n käsittelyn viereen:
```javascript
const _tier = api.status?._raw?.elastic_scaler?.tier?.toUpperCase() || "—";
```

Ja `aw`-taulukkoon ennen `{k:"Cloud"...}`:
```javascript
{k:"Tier",v:_tier,c:_tier!=="—"?"#A78BFA":"rgba(255,255,255,.20)"},
```

### D3: Validoi

```bash
cd dashboard && npx vite build    # OK
```

---

## BLOKKI E: Voikko-portaabelius + CodeReview wiring (1h)

### E1: Voikko-polku dynaamiseksi (3 tiedostoa)

**Tiedostot:**
- `core/normalizer.py` (~rivi 77)
- `translation_proxy.py` (~rivi 51)
- `core/auto_install.py` (~rivi 76)

Jokaisessa tiedostossa korvaa:
```python
r"U:\project2\voikko"
```

Tällä:
```python
str(Path(__file__).resolve().parent.parent / "voikko") if (Path(__file__).resolve().parent.parent / "voikko").exists() else r"U:\project2\voikko"
```

**TAI** yksinkertaisemmin, lisää tiedoston alkuun apufunktio:
```python
def _voikko_path():
    """Find voikko directory relative to project root."""
    project = Path(__file__).resolve().parent
    if project.name == "core":
        project = project.parent
    vp = project / "voikko"
    if vp.exists():
        return str(vp)
    return r"U:\project2\voikko"  # fallback
```

Ja käytä `_voikko_path()` hardcoded polun tilalla.

**Huom:** `core/auto_install.py` ja `core/normalizer.py` ovat `core/`-kansiossa, joten `parent.parent` vie oikeaan paikkaan. `translation_proxy.py` on juuressa, joten siellä `parent / "voikko"` riittää.

### E2: CodeSelfReview — tarkista dashboard wiring

Lue `web/dashboard.py` rivit ~873-904. Siellä on jo:
- `GET /api/code_suggestions` — palauttaa pending suggestions
- `POST /api/code_suggestions/{index}/accept`
- `POST /api/code_suggestions/{index}/reject`

Tarkista `core/code_reviewer.py`:
- `accept_suggestion(index)` — tallentaako tuloksen?
- `reject_suggestion(index)` — tallentaako?
- `_save_suggestions()` — kutsutaanko stop():ssa? (hivemind.py:1051-1055 ✅)

Jos kaikki on kunnossa, ei tarvita muutoksia. Jos `accept/reject` ei tallenna tiedostoon, lisää `self._save_suggestions()` kummankin metodin loppuun.

### E3: Validoi

```bash
python -c "from core.normalizer import FinnishNormalizer; print('OK')"
python -c "from translation_proxy import TranslationProxy; print('OK')"
```

---

## BLOKKI F: CHANGELOG + validointi (1h)

### F1: CHANGELOG.md

Lisää v0.0.8 entry tiedoston alkuun (ennen v0.0.7):

```markdown
## v0.0.8 (2026-03-04) — Phase 11 Wiring, Test Expansion, Code Quality

### Phase 11: ElasticScaler Wired
- `hivemind.py`: ElasticScaler imported, initialized in start(), tier info in get_status()
- Auto-detects hardware at startup: CPU, RAM, GPU VRAM → selects tier (minimal/light/standard/professional/enterprise)
- ZBook correctly detected as "standard" tier (8GB VRAM, 128GB RAM)

### New Test Suites (#33-#35)
- `tests/test_elastic_scaler.py` — 15 tests (syntax, dataclasses, tier classification, runtime)
- `tests/test_training_collector.py` — 15 tests (syntax, core logic, integration)
- `tests/test_swarm_routing.py` — 12 tests (syntax, scheduler, roles, agents)
- `tests/test_smart_home.py` — +4 AudioMonitor integration tests

### Code Quality: print() → structured logging
- `hivemind.py`: 74 print() → log.info/warning/error (startup, shutdown, status messages)
- `core/adaptive_throttle.py`: 14 print() → log.*
- `core/yaml_bridge.py`, `learning_engine.py`, `live_monitor.py`, `ops_agent.py`, `knowledge_loader.py`: 28 print() → log.*
- Exception: `core/auto_install.py` keeps print() (runs before logging init)

### Dashboard
- Test count: 30/30 → 35/35 (EN + FI + boot + statusbar)
- Tier indicator: shows current ElasticScaler tier in status bar

### Portability
- Voikko paths: hardcoded `r"U:\project2\voikko"` → dynamic Path resolution with fallback (3 files)

### Full Suite: 35/35 GREEN, Health Score 100/100
```

### F2: Aja koko testisuite

```bash
python tools/waggle_backup.py --tests-only
```

Odotettu tulos: **35/35 GREEN, 700+ ok, 0 fail, Health Score 100/100**

### F3: Dashboard build

```bash
cd dashboard && npx vite build
```

### F4: Päivitä MEMORY.md

Tiedosto: `C:\Users\mfi0jjko\.claude\projects\U--project2\memory\MEMORY.md`

Päivitä:
- `waggle_backup.py` — "32 test suites" → "35 test suites"
- Test Status: "32 suites GREEN" → "35 suites GREEN"
- Lisää Phase 11: ✅ COMPLETE kohtaan Completed Phases
- Lisää lyhyt kuvaus v0.0.8 muutoksista

---

## YHTEENVETO — TOTEUTUSJÄRJESTYS

```
A1. Wire ElasticScaler hivemind.py    (import, __init__, start(), get_status())
A2. Luo tests/test_elastic_scaler.py  (15 testiä)
A3. Rekisteröi suite #33              (waggle_backup.py)
A4. ✓ Validoi A
B1. Luo tests/test_training_collector.py  (15 testiä)
B2. Luo tests/test_swarm_routing.py       (12 testiä)
B3. Lisää 4 testiä test_smart_home.py     (AudioMonitor)
B4. Rekisteröi suitet #34 ja #35
B5. ✓ Validoi B
C1. hivemind.py print→log             (74 muutosta)
C2. core/*.py print→log               (28 muutosta, auto_install.py skipataan)
C3. ✓ Validoi C
D1. App.jsx 30/30→35/35               (8 kohtaa EN+FI+boot+statusbar)
D2. App.jsx Tier-indikaattori          (statusbar)
D3. ✓ Validoi D (vite build)
E1. Voikko-polku dynaamiseksi          (3 tiedostoa)
E2. CodeReview wiring tarkistus        (lue, korjaa tarvittaessa)
E3. ✓ Validoi E
F1. CHANGELOG.md v0.0.8
F2. ✓ Full suite 35/35
F3. ✓ Dashboard build
F4. Päivitä MEMORY.md
```

**Lopputulos:** 35/35 GREEN, Phase 11 live, print-siivous tehty, dashboard ajan tasalla.

# WaggleDance Swarm AI — Diagnostiikkaraportti

**Päivämäärä:** 2026-03-10
**Versio:** v0.9.0
**Branch:** master (linear, ei muita brancheja)
**HEAD:** `7e47386` — `fix(auto): _load_persisted_facts_count crash before _night_mode init`

---

## 1. Git-historia (viimeiset 10 commitia)

```
7e47386 fix(auto): _load_persisted_facts_count crash before _night_mode init
c61d194 fix: test_c1_hotcache finds real method body after controller extraction
1bdb639 docs: v0.9.0 session documentation, CHANGELOG, ARCHITECTURE update
cde2958 feat: v0.9.0 — hivemind refactor, Phi-3.5-mini LoRA, Sonnet review fixes
90f4583 docs: add v0.8.0 session report
6f34090 feat: v0.8.0 — 12 bug fixes (5 critical, 7 high priority)
3f16f0a fix: Phase 1+2 — 12 critical and high-priority bug fixes
3824210 fix: TrustEngine wiring, NightEnricher wiring, datetime timezone fixes
4287659 fix: update test_b4 assertion for refactored _do_chat local variables
d108144 fix: 31 bug fixes + comprehensive documentation update (v0.7.0)
```

---

## 2. CI-status (GitHub Actions)

```
7e47386  completed  success    2026-03-09T17:10:05Z  ← HEAD, GREEN
c61d194  completed  success    2026-03-09T16:17:37Z  ← CI fix
1bdb639  completed  failure    2026-03-09T15:38:29Z  ← docs commit (test_c1 crashed)
cde2958  completed  failure    2026-03-09T14:49:28Z  ← v0.9.0 release (test_c1 crashed)
90f4583  completed  success    2026-03-09T09:56:06Z
6f34090  completed  success    2026-03-09T09:32:35Z
```

**CI-vikaanalyysi:** Commitit `cde2958` ja `1bdb639` rikkoivat CI:n koska `test_c1_hotcache.py` etsi `_populate_hot_cache`-metodin runkoa 500 merkin ikkunalla. Refaktoroinnin jälkeen delegaattistubi löytyi ensin (3 riviä, ei sisällä `_is_valid_response`-kutsua). Korjaus: `c61d194` muutti testin käyttämään 800 merkin ikkunaa ja etsimään oikean toteutuksen (se jossa on `hot_cache.put()`).

---

## 3. Paikallinen testitulos (backup 2026-03-09 18:14)

```
Suites: 44/45 PASS, 1 CRASH
Tests:  699 ok, 0 fail, 14 warn
Health: 90/100
```

**CRASH:** `test_c1_hotcache.py` — korjattu commitissa `c61d194` (ei ollut mukana backup-ajon hetkellä).

**14 warningia:** Kaikki ajoitusten toleransseihin liittyviä, ei toiminnallisia ongelmia.

---

## 4. Working tree -tila

```
master branch, up to date with origin/master

Muokatut (unstaged):
  configs/confusion_memory.json  — TestiAgentti counter 71 → 72 (testien sivuvaikutus)

Untracked:
  restore.bat  — One-click restore -skripti (5.4 KB, toiminnallinen)
```

**Ei stasheja.**

---

## 5. Versioiden yhdenmukaisuus

| Tiedosto | Versio |
|----------|--------|
| `main.py` | `v0.9.0 • Built: 2026-03-09` |
| `pyproject.toml` | `version = "0.9.0"` |
| `hivemind.py` | `Swarm Queen v0.9.0` |
| `CHANGELOG.md` | `## [0.9.0] — 2026-03-09` |
| `README.md` | `version-0.9.0` badge |

**Kaikki yhtenevät.** ✓

---

## 6. Avaintiedostojen rivimäärät

| Tiedosto | Rivit | Rooli |
|----------|-------|-------|
| `hivemind.py` | 1393 | Pääorchestrator + thin delegates |
| `core/chat_handler.py` | 716 | Chat-reititys, swarm, multi-agent |
| `core/heartbeat_controller.py` | 662 | Heartbeat-silmukka, proaktiivinen ajattelu |
| `core/round_table_controller.py` | 476 | Round Table -debatit, agentivalinta |
| `core/night_mode_controller.py` | 455 | Yöoppimissykli, konvergenssi |
| `web/dashboard.py` | 1372 | FastAPI production endpoints |
| `tools/train_micromodel_v3.py` | 466 | LoRA-treenausputki |
| `main.py` | 239 | Entry point |
| **Yhteensä** | **5779** | 8 avaintiedostoa |

hivemind.py oli aiemmin 3321 riviä → nyt 1393 + 4 kontrolleria (yhteensä 2309 riviä siirretty).

---

## 7. v0.9.0 Refaktorointi — mikä muuttui

### Extracted controllers (proxy-pattern: `__getattr__`/`__setattr__`)

```
hivemind.py (3321 lines)
    ├─→ core/chat_handler.py          (716 lines, 7 methods)
    ├─→ core/night_mode_controller.py  (455 lines, 8 methods)
    ├─→ core/round_table_controller.py (476 lines, 5 methods)
    └─→ core/heartbeat_controller.py   (662 lines, 10 methods)
    = hivemind.py (1393 lines, 30 thin delegates)
```

Proxy-pattern: jokainen kontrolleri saa `self.hivemind` referenssin, ja `__getattr__`/`__setattr__` ohjataan HiveMind-instanssiin. Näin alkuperäiset metodirungot toimivat sellaisenaan ilman `self.X` → `self.hivemind.X` muutoksia.

### Sonnet review -korjaukset (12 kpl)

| ID | Tiedosto | Korjaus |
|----|----------|---------|
| C2 | audit_log.py, trust_engine.py | `_write_lock` luokka→instanssi |
| C3 | web/dashboard.py | `/api/voice/audio` 10MB body limit |
| H2 | shared_memory.py | `recall()` LIKE wildcard escaping |
| H3 | web/dashboard.py | `/api/profile` atomic write |
| H4 | web/dashboard.py | `/api/chat` JSON parse → 400 |
| H5 | web/dashboard.py | MAGMA route errors logged |
| H6 | web/dashboard.py | `/api/history` param validation |
| L1 | agents/spawner.py | Duplicate import removed |
| L6 | backend/routes/chat.py | `os.replace()` atomic write |
| M2 | hivemind.py | `_notify_ws` snapshot copy |
| M6 | core/cognitive_graph.py | BFS `deque` instead of `list.pop(0)` |
| M7 | main.py | nvidia-smi `asyncio.create_subprocess_exec()` |

### LoRA-pipeline (Phi-3.5-mini)

```json
{
  "base_model": "microsoft/Phi-3.5-mini-instruct",
  "samples": 100,
  "epochs": 1,
  "learning_rate": 0.0002,
  "final_loss": null,
  "peak_vram_gb": 2.92,
  "backend": "peft"
}
```

- 4-bit NF4 quantization + double quant
- CPU offloading: `max_memory={0: "6GiB", "cpu": "40GiB"}`
- Auto-detection: fused `qkv_proj` (Phi-3.5) vs. erillinen `q_proj`/`v_proj`
- Adapteri: `models/micromodel_v3_phi35/lora_adapter/` (96 MB)
- Täysi treeni (5000+ samples) keskeytettiin — liian hidas RTX A2000:lla

---

## 8. Tunnetut ongelmat ja avoimet asiat

### P1: `_load_persisted_facts_count` init-järjestys (KORJATTU: `7e47386`)
hivemind.py kutsuu `_load_persisted_facts_count()` rivillä ~136, mutta `_night_mode` kontrolleri luodaan vasta rivillä ~177. Korjaus: safe fallback lukee suoraan `learning_progress.json` jos kontrolleri ei ole vielä alustettu.

### P2: `test_c1_hotcache.py` CRASH (KORJATTU: `c61d194`)
Testi etsi delegate-stubin 500 merkin ikkunalla, eikä löytänyt `_is_valid_response`-kutsua. Korjaus: skannaa kaikki esiintymät 800 merkin ikkunalla.

### P3: `configs/confusion_memory.json` (AVOIN, triviaali)
TestiAgentti-laskuri noussut 71→72 testien sivuvaikutuksena. Ei vaikutusta tuotantoon.

### P4: `restore.bat` untracked (AVOIN, harmitonta)
One-click restore -skripti, ei commitoitu. Voidaan lisätä tai jättää.

### P5: LoRA-treeni keskeneräinen (DEFERRED)
Vain 100 näytteen validointiajo tehty (peak 2.92 GB VRAM). `final_loss: null` koska raporttiin ei tallennettu viimeistä loss-arvoa. Täysi 5000+ samples treeni vaatii 4+ tuntia GPU-aikaa.

### P6: hivemind.py docstring vanhentunut (MINOR)
Rivi 9: `v0.0.2 MUUTOKSET:` — vanhat fix-muistiinpanot ovat yhä docstringissä. Voitaisiin siivota.

### P7: PAT-token puuttuu `workflow` scope (AVOIN)
GitHub PAT ei pysty pushaamaan `.github/workflows/` -tiedostoja. Jos CI-workflowia pitää muuttaa, PAT tarvitsee uuden scopen.

---

## 9. Backup-tila

| Backup | Koko | Tiedostoja | Aika |
|--------|------|------------|------|
| `waggle_20260309_181410.zip` | 327.7 MB | 654 | 2026-03-09 18:14 |
| `waggle_20260309_115454.zip` | 148.4 MB | 654 | 2026-03-09 11:55 |
| `waggle_20260309_091509.zip` | 146.9 MB | 654 | 2026-03-09 09:15 |

Kopiot: `C:\WaggleDance_Backups` + `D:\WaggleDance_Backups` (rotaatio 7 kpl).

**Huom:** Viimeisin backup (`181410`) otettiin ENNEN `test_c1_hotcache.py` -korjausta ja `_load_persisted_facts_count` -fiksausta. Nämä ovat 2 commitia (`c61d194`, `7e47386`) joita ei ole backupissa.

---

## 10. Projektin kokonaistila

```
Lähdekoodi:    577 tiedostoa, 20.1 MB
Agentit:       128 YAML-tiedostoa (75 uniikkia agenttia, 4 profiilia)
Testisuiteja:  46 tiedostoa (45 ajettavaa, 1 crash korjattu)
CI:            GREEN (commit 7e47386)
Versio:        v0.9.0, kaikki 5 lähdetiedostoa yhtenevät
Ollama:        phi4-mini, llama3.2:1b, nomic-embed-text, all-minilm + deepseek-r1:1.5b, qwen3
HW:            RTX A2000 8GB, Intel 12th Gen, 128 GB RAM, Windows 11
Python:        3.13.7
```

---

## 11. Suositukset seuraavalle sessiolle

1. **Ota uusi backup** — nykyinen backup puuttuu 2 commitista
2. **Commitoi tai hylkää** `configs/confusion_memory.json` (TestiAgentti 71→72)
3. **Päätä `restore.bat`** — commitoi tai .gitignore
4. **Siivoa hivemind.py docstring** — poista vanhat v0.0.2 muistiinpanot
5. **LoRA täystreeni** — aja `python tools/train_micromodel_v3.py` pidemmällä sessiolla (4+ h)
6. **Päivitä PAT** `workflow`-scopella jos CI-workflowia pitää muuttaa

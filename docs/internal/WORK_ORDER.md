# WORK_ORDER — New Runtime Rollout

**Date:** 2026-03-14
**Prereqs:** CURRENT_STATUS.md, ARCHITECTURE_DECISIONS.md, INTEGRATION_COMPLETE.md, MIGRATION_STATUS.md

---

## 1. Tavoite

Siirtää kehityksen ensisijainen entrypoint legacy-polusta (`main.py` + `start.py`) uuteen runtime-polkuun (`waggledance.adapters.cli.start_runtime`). Varmistaa, että uusi polku toimii käytännön ajossa (stub + non-stub). Legacy-polut säilyvät yhteensopivuutta varten mutta merkitään virallisesti toissijaisiksi.

---

## 2. Rajaus

**IN-SCOPE:**
- `start_runtime.py`: puuttuvat ominaisuudet (argparse, host/port-konfiguraatio, log_level, Ollama-tarkistus, UTF-8 Windows-korjaus)
- Stub- ja non-stub-smoke-testit uudelle entrypointille
- `start.py`: lisää vaihtoehto 4 "New runtime (hexagonal)" interaktiiviseen menuun
- `pyproject.toml [project.scripts]`: tarkista toimivuus
- Entrypoint-dokumentaatio: RECOMMENDED.md tai README-osio
- Legacy-polkujen merkintä deprecated-tilaan (docstring + print-varoitus)

**OUT-OF-SCOPE:**
- Dashboard-uudistus / React build pipeline
- Uudet route-tyypit (micromodel, rules)
- Legacy-koodin poistaminen (ei ennen 24h validointia)
- Uudet arkkitehtuurikerrokset
- Agent spawn -logiikan porttaaminen uuteen runtimeen (jatkokehitys)

---

## 3. Nykytila-analyysi

### `start_runtime.py` (uusi, 27 riviä)
- Minimaalinen: `WaggleSettings.from_env()` → `Container(stub=)` → `build_app()` → `uvicorn.run()`
- Puuttuu: argparse, host/port override, log_level, Windows UTF-8, Ollama-check, profiilinvalinta
- Ei käynnistä agentteja (HiveMind-agentit eivät kuulu uuteen arkkitehtuuriin vielä)

### `main.py` (legacy, 256 riviä)
- Tekee kaiken: UTF-8, auto_install, HiveMind init, agent spawn (25 kpl), FAISS, bilingval cache, dashboard, uvicorn
- Tuotannon ainoa todellinen entrypoint tällä hetkellä

### `start.py` (legacy launcher, 209 riviä)
- Interaktiivinen menu (stub/production/profiilinvaihto)
- Stub-moodi käynnistää `backend.main:app` — ei uutta `waggledance`-runtimea
- Production-moodi ajaa `main.py`

### `pyproject.toml [project.scripts]`
- `waggledance = "waggledance.adapters.cli.start_runtime:main"` — konfiguroitu, ei testattu pip-installin kautta

---

## 4. Muutettavat tiedostot

| Tiedosto | Muutos |
|----------|--------|
| `waggledance/adapters/cli/start_runtime.py` | Argparse (--stub, --host, --port, --log-level), Windows UTF-8, Ollama-health-check (non-stub), banner |
| `start.py` | Lisää vaihtoehto "3. NEW RUNTIME" menuun, deprecated-varoitus vanhoille moodeille |
| `main.py` | Docstring-päivitys: "Legacy entrypoint. See start_runtime.py for new architecture." |

---

## 5. Uudet tiedostot

| Tiedosto | Tarkoitus |
|----------|-----------|
| `tests/integration/test_runtime_smoke.py` | Stub-smoke: Container(stub=True).build_app() → TestClient → /health 200, /ready 200, /api/chat 200 |
| `tests/integration/test_runtime_cli.py` | CLI argparse: --stub, --host, --port, --log-level parsitaan oikein |
| `ENTRYPOINTS.md` | Recommended entrypoint -dokumentaatio (mikä on primary, mikä legacy) |

---

## 6. Testit

| Testi | Mitä varmistaa |
|-------|----------------|
| `test_runtime_smoke::test_stub_health` | GET /health → 200 stub-moodissa |
| `test_runtime_smoke::test_stub_ready` | GET /ready → 200 stub-moodissa |
| `test_runtime_smoke::test_stub_chat` | POST /api/chat → 200 stub-moodissa |
| `test_runtime_smoke::test_non_stub_container` | Container(stub=False).memory_repository on ChromaMemoryRepository |
| `test_runtime_cli::test_parse_stub` | --stub → stub=True |
| `test_runtime_cli::test_parse_host_port` | --host 127.0.0.1 --port 9000 → oikeat arvot |
| `test_runtime_cli::test_parse_log_level` | --log-level debug → "debug" |
| `test_runtime_cli::test_default_values` | Ei argumentteja → host=0.0.0.0, port=8000, stub=False |
| Olemassa olevat (172 + 946) | Ei regressiota |

---

## 7. Acceptance Criteria

| # | Kriteeri |
|---|---------|
| R-1 | `python -m waggledance.adapters.cli.start_runtime --stub` käynnistyy ilman crashia |
| R-2 | `python -m waggledance.adapters.cli.start_runtime --stub --port 9000` kuuntelee portissa 9000 |
| R-3 | Stub-moodissa /health, /ready, /api/chat palauttavat 200 |
| R-4 | Non-stub-moodissa Container käyttää ChromaMemoryRepository (ei InMemory) |
| R-5 | Windows UTF-8 (chcp 65001 + PYTHONUTF8) toimii start_runtime.py:ssä |
| R-6 | `start.py` näyttää uuden runtime-vaihtoehdon ja deprecated-varoituksen |
| R-7 | `main.py` docstring kertoo legacy-statuksen |
| R-8 | `tests/integration/test_runtime_smoke.py` — kaikki PASS |
| R-9 | `tests/integration/test_runtime_cli.py` — kaikki PASS |
| R-10 | Olemassa olevat 172 + 946 testiä — kaikki PASS (ei regressiota) |
| R-11 | `ENTRYPOINTS.md` dokumentoi primary vs legacy -polut |

---

## 8. Riskit

| Riski | Mitigaatio |
|-------|-----------|
| Non-stub smoke vaatii Ollama + ChromaDB | Testaa vain Container-konstruktiota, ei HTTP-kutsuja non-stub-moodissa |
| Windows UTF-8 -korjaus voi vaikuttaa CI:hin | Ehdollinen `sys.platform == "win32"` (sama kuin main.py:ssä) |
| `pyproject.toml` script ei toimi ilman pip install -e | Dokumentoi: `pip install -e .` tai `python -m waggledance.adapters.cli.start_runtime` |
| start.py -muutos rikkoo olemassa olevan käyttäjäkokemuksen | Lisää vaihtoehto, ei poista vanhoja — backward compatible |

---

## 9. Mitä EI tehdä tässä

1. **Ei agent spawn -logiikkaa** — uusi runtime ei käynnistä HiveMind-agentteja (se on jatkokehitystä: AgentPort + AgentSpawnerAdapter)
2. **Ei dashboard-buildia** — React build on erillinen prosessi
3. **Ei micromodel/rules-palautusta** — ALLOWED_ROUTE_TYPES pysyy {hotcache, memory, llm, swarm}
4. **Ei legacy-koodin poistamista** — main.py, start.py, hivemind.py säilyvät
5. **Ei CI/CD-pipelinea** — GitHub Actions on erillinen työ
6. **Ei FAISS/bilingual/fi_fast -populointia** — kuuluu HiveMind-kerrokseen, ei uuteen runtimeen
7. **Ei uusia portteja tai adaptereita** — käytetään olemassa olevia 8 porttia

---

## 10. Toteutusjärjestys

1. ~~`start_runtime.py` — argparse + Windows UTF-8 + Ollama-check + banner~~ DONE
2. ~~`tests/integration/test_runtime_cli.py` — CLI-parsintätestit~~ DONE (8 tests)
3. ~~`tests/integration/test_runtime_smoke.py` — HTTP-smoke-testit (TestClient)~~ DONE (8 tests)
4. ~~`start.py` — uusi vaihtoehto + deprecated-varoitus~~ DONE
5. ~~`main.py` — docstring-päivitys~~ DONE
6. ~~`ENTRYPOINTS.md` — dokumentaatio~~ DONE
7. ~~Olemassa olevien testien regressioajo (172 + 946)~~ DONE

---

## 11. Tulokset (2026-03-14)

| Testi | Tulos |
|-------|-------|
| `tests/integration/test_runtime_cli.py` | 8/8 PASS |
| `tests/integration/test_runtime_smoke.py` | 8/8 PASS |
| `tests/unit/ + unit_core/ + unit_app/ + contracts/` | 172/172 PASS |
| `tools/waggle_backup.py --tests-only` | 72/72 suites, 946 ok, 0 fail, Health 100/100 |
| **Grand total** | **1134 tests, 0 failures** |

Kaikki 11 acceptance-kriteeriä (R-1 – R-11) täyttyvät.

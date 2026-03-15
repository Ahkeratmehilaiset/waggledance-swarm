# Contributing to WaggleDance

## Setup

1. Clone the repo and install dependencies:
   ```bash
   git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
   cd waggledance-swarm
   pip install -r requirements.txt
   ```

2. Install Ollama and pull required models:
   ```bash
   ollama pull phi4-mini
   ollama pull nomic-embed-text
   ollama pull all-minilm
   ```

3. Validate the environment:
   ```bash
   python tools/waggle_restore.py
   ```

## Running Tests

### Legacy tests (subprocess-based):
```bash
python tests/run_all.py --skip-ollama
```

### Pytest suites:
```bash
# Unit + core + app + contracts
python -m pytest tests/unit/ tests/unit_core/ tests/unit_app/ tests/contracts/ -v

# Integration
python -m pytest tests/integration/ -v

# All pytest suites
python -m pytest tests/unit/ tests/unit_core/ tests/unit_app/ tests/contracts/ tests/integration/ -q
```

### Compile check:
```bash
python -m compileall core memory web waggledance backend integrations tests -q
```

## Runtime

### Legacy runtime:
```bash
python start.py
```

### New hexagonal runtime:
```bash
python -m waggledance.adapters.cli.start_runtime --stub    # stub mode (no Ollama)
python -m waggledance.adapters.cli.start_runtime           # production mode
```

## Architecture

- `core/` — Legacy brain layer (memory_engine, chat_handler, etc.)
- `waggledance/` — New hexagonal architecture
  - `core/domain/` — Pure domain models (dataclasses)
  - `core/ports/` — Protocol interfaces
  - `core/orchestration/` — Routing, scheduling
  - `core/policies/` — Confidence, fallback, escalation
  - `adapters/` — Port implementations (SQLite, Ollama, HTTP)
  - `application/` — Services and use cases

## Adding Agents

1. Create a YAML file in `configs/agents/`:
   ```yaml
   id: my_agent
   name: My Agent
   domain: general
   tags: [my_tag]
   profile: ALL
   ```
2. Add routing keywords in `core/hive_routing.py` under `WEIGHTED_ROUTING`
3. Write tests in `tests/`

## Pull Requests

- Run all tests before submitting
- Keep PRs focused on a single concern
- Include test coverage for new features
- Update CHANGELOG.md for user-visible changes

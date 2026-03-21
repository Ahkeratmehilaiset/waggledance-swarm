# WaggleDance Deployment Guide

*Asennusohje — Installation and configuration*

## Quick Start (Docker)

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
cp .env.example .env        # Edit with your secrets
docker compose up -d
```

Pull required models (one-time, ~3GB total):

```bash
docker compose exec ollama ollama pull phi4-mini
docker compose exec ollama ollama pull llama3.2:1b
docker compose exec ollama ollama pull nomic-embed-text   # CRITICAL
docker compose exec ollama ollama pull all-minilm
```

Open **http://localhost:8000**

> `nomic-embed-text` is required for all memory operations. WaggleDance refuses to start without it.

### GPU Support

- **Linux:** Install [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- **Windows:** Docker Desktop with WSL2 GPU support
- **No GPU?** Remove the `deploy.resources` block from `docker-compose.yml`

---

## Native Install

### Windows

```powershell
# 1. Install Ollama from https://ollama.ai
# 2. Install Python 3.13+ from https://python.org

# 3. Pull models
ollama pull phi4-mini
ollama pull llama3.2:1b
ollama pull nomic-embed-text
ollama pull all-minilm

# 4. Clone and install
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
pip install -r requirements.txt   # or: pip install -e .

# 5. Run (legacy)
python main.py

# 5b. Run (new runtime)
python -m waggledance.adapters.cli.start_runtime
```

### Linux / macOS

```bash
# 1. Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# 2. Pull models
ollama pull phi4-mini && ollama pull llama3.2:1b
ollama pull nomic-embed-text && ollama pull all-minilm

# 3. Clone and install
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
pip install -r requirements.txt   # or: pip install -e .

# 4. Run (legacy)
python main.py

# 4b. Run (new runtime)
python -m waggledance.adapters.cli.start_runtime
```

---

## Launcher

```bash
python start.py              # Interactive menu
python start.py --stub       # No Ollama — stub backend + React dashboard
python start.py --production # Full HiveMind (requires Ollama + 4 models)
```

---

## Deployment Profiles

| Profile | Target Hardware | Use Case |
|---------|----------------|----------|
| **GADGET** | ESP32, RPi, wearables | TinyML classifiers, sensor fusion *(theoretical)* |
| **COTTAGE** | Any PC with GPU | Off-grid monitoring, environmental sensing, weather |
| **HOME** | Mac Mini, NUC, desktop | Home automation, voice control, energy optimization |
| **FACTORY** | Server rack, DGX | Predictive maintenance, monitoring *(theoretical)* |

---

## Hardware Requirements

| Tier | VRAM | RAM | Models | Agents |
|------|------|-----|--------|--------|
| MINIMAL | <2GB | 4GB | None (V1 pattern match only) | 0 |
| LIGHT | 2-4GB | 8GB | qwen3:0.6b + smollm2:135m | 2 |
| STANDARD | 4-16GB | 16GB+ | phi4-mini + llama3.2:1b | 6 |
| PROFESSIONAL | 16-48GB | 32GB+ | phi4:14b + qwen3:4b | 15 + vision |
| ENTERPRISE | 48GB+ | 64GB+ | llama3.3:70b + llama3.1:8b | 75 |

Hardware is auto-detected at startup by `core/elastic_scaler.py`. Only STANDARD tier is tested in production.

---

## Environment Variables

Copy `.env.example` to `.env`:

```env
# Core
WAGGLE_PROFILE=COTTAGE
OLLAMA_HOST=http://localhost:11434
WAGGLE_CHAT_MODEL=phi4-mini
WAGGLE_LEARNING_MODEL=llama3.2:1b
WAGGLE_EMBED_MODEL=nomic-embed-text
WAGGLE_API_KEY=

# GitHub PAT (for backup/push)
GITHUB_PAT=

# Telegram alerts (Phase 5)
WAGGLEDANCE_TELEGRAM_BOT_TOKEN=
WAGGLEDANCE_TELEGRAM_CHAT_ID=

# Home Assistant integration (Phase 5)
WAGGLEDANCE_HA_TOKEN=

# Knowledge distillation API key (Phase D)
WAGGLEDANCE_DISTILLATION_API_KEY=
```

Additional environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `WAGGLE_PROFILE` | `COTTAGE` | Deployment profile (GADGET/COTTAGE/HOME/FACTORY) |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `WAGGLE_CHAT_MODEL` | `phi4-mini` | Primary chat model |
| `WAGGLE_LEARNING_MODEL` | `llama3.2:1b` | Background learning model |
| `WAGGLE_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `WAGGLE_API_KEY` | *(auto-generated)* | API bearer token |
| `PYTHONUTF8` | `1` | Force UTF-8 on Windows |
| `CORS_ORIGINS` | `localhost:5173,localhost:3000` | Allowed CORS origins |

---

## Verify Installation

```bash
# Run all 79 legacy test suites
python tools/waggle_backup.py --tests-only

# Run all pytest tests (unit, core, app, contracts, integration, autonomy)
python -m pytest tests/ -v

# Validate environment
python tools/waggle_restore.py
```

Expected: **79 legacy suites GREEN (~1468 assertions), 4129 pytest tests = ~5600 total tests.**

---

## Docker Healthcheck

The Docker container includes a healthcheck that pings `/health` every 30 seconds:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 30s
  timeout: 5s
  retries: 3
```

---

## Night Shift (Unattended Operation)

```bash
python tools/night_shift.py
```

Runs WaggleDance as a subprocess with:
- Health check every 5 minutes
- Watchdog: 3 failures → restart (max 5)
- Morning report at shutdown
- Windows Task Scheduler compatible

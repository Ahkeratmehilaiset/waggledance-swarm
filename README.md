# WaggleDance Swarm AI

> Local-first multi-agent AI runtime with solver-first architecture, autonomous overnight learning, and full audit trail.

[![Tests](https://img.shields.io/badge/tests-4350%20passing-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![License](https://img.shields.io/badge/license-Apache%202.0%20%2B%20BUSL%201.1-orange)]()

## What is this?

WaggleDance is a **local-first AI runtime** that routes tasks through memory, rules, statistics, and model-based inference — in that order. LLM is the last resort, not the first.

It runs on anything from a Raspberry Pi (5 agents) to a factory server (75 agents), with overnight self-learning, full provenance audit, and zero cloud dependency.

## Architecture

```
waggledance/                    <- Hexagonal runtime (primary)
  core/
    autonomy/               <- Runtime, resource kernel, attention budget
    world/                  <- World model, epistemic uncertainty, graph
    reasoning/              <- 10 solver engines (thermal, causal, anomaly...)
    learning/               <- Dream mode, consolidator, night pipeline
    specialist_models/      <- Specialist trainer, meta-optimizer, model store
    goals/                  <- Goal engine, motives, mission store
    planning/               <- Planner
    policy/                 <- Policy engine, risk scoring, constitution
    actions/                <- Safe Action Bus (deny-by-default)
    capabilities/           <- Registry, selector, aliasing
    projections/            <- Narrative, introspection, autobiographical (read-only)
    magma/                  <- Audit, provenance, replay, trust, confidence decay
    domain/                 <- CaseTrajectory, Goal, WorldSnapshot dataclasses
  adapters/                   <- LLM, memory, sensors, HTTP, config
  application/                <- Services, DTOs, use cases
  bootstrap/                  <- DI container, capability loader

core/                           <- Legacy modules (still functional)
  safe_eval.py                <- AST-based expression evaluator (replaced eval() RCE)
  hallucination_checker.py    <- 5-signal detection (grounding, consistency, corrections)
  rag_verifier.py             <- RAG-based fact verification
  memory_engine.py            <- ChromaDB + FAISS + bilingual embeddings
  micro_model.py              <- V1 (regex), V2 (PyTorch MLP), V3 (PEFT QLoRA)
  resource_guard.py           <- OOM protection, throttling, emergency GC
  chat_router.py              <- Unified routing (autonomy -> legacy -> fallback)
  tracing.py                  <- OpenTelemetry distributed tracing
```

## Key Features (verified in code, not just docs)

| Feature | File | Lines | What it does |
|---------|------|------:|--------------|
| Dream Mode | `waggledance/core/learning/dream_mode.py` | 367 | Counterfactual simulation overnight |
| Case Trajectories | `waggledance/core/domain/autonomy.py` | 493 | Goal -> snapshot -> action -> outcome learning |
| World Model | `waggledance/core/world/world_model.py` | 268 | Self-entity, snapshots, epistemic state |
| Solver-first routing | `waggledance/core/reasoning/solver_router.py` | 370 | 10 engines before LLM |
| Safe Action Bus | `waggledance/core/actions/action_bus.py` | 240 | Deny-by-default for writes |
| Specialist training | `waggledance/core/specialist_models/specialist_trainer.py` | 890 | 14 sklearn models (TF-IDF, RF, Ridge...) |
| LoRA V3 training | `core/micro_model.py` | 1218 | PEFT QLoRA fine-tuning |
| Hallucination detection | `core/hallucination_checker.py` | 212 | 5 signals + RAG verification |
| MAGMA audit trail | `waggledance/core/magma/` | 1151 | Provenance, replay, trust, confidence decay |
| Prometheus metrics | `core/observability.py` | 45 | /metrics endpoint |
| OpenTelemetry tracing | `core/tracing.py` | 81 | Distributed tracing with OTLP export |
| OOM protection | `core/resource_guard.py` | 123 | Memory/disk/CPU monitoring + emergency GC |

## Quick Start

```bash
# Clone
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm

# Docker (recommended)
docker compose up -d

# Native (requires Ollama running locally)
pip install -r requirements.txt
python start_waggledance.py --preset=cottage-full
```

Dashboard: http://localhost:8000

## Deployment Profiles

| Profile | Target | Hardware | Use Cases |
|---------|--------|----------|-----------|
| **GADGET** | Edge / IoT | RPi, ESP32, Jetson Nano | Sensor calibration, battery optimization, environmental monitoring |
| **COTTAGE** | Off-grid property | Mini-PC, NUC | Heating control, frost protection, energy management |
| **HOME** | Smart home | Desktop, NAS, GPU workstation | Comfort automation, safety, energy optimization |
| **FACTORY** | Industrial | Server, DGX, on-prem cluster | OEE, SPC, predictive maintenance. MQTT, ROS 2, OPC-UA, Modbus. ISO/GMP audit trail. |

### Hardware Presets (one-command deployment)

| Preset | Profile | Agents | Ollama Model | V2 Training | V3 LoRA |
|--------|---------|-------:|--------------|:-----------:|:-------:|
| `raspberry-pi-iot` | GADGET | 5 | phi4-mini | - | - |
| `cottage-full` | COTTAGE | 30 | llama3.2:3b | yes | - |
| `factory-production` | FACTORY | 75 | llama3.2:3b | yes | yes |

```bash
python start_waggledance.py --preset=raspberry-pi-iot     # 5 agents, minimal
python start_waggledance.py --preset=cottage-full          # 30 agents, outdoor
python start_waggledance.py --preset=factory-production    # 75 agents, industrial
python start_waggledance.py --stub                         # No Ollama needed
```

## 3-Layer Architecture

| Layer | Role | Examples |
|-------|------|----------|
| **Layer 3** (Authoritative) | Decides | Solvers, World Model, Policy Engine, Verifier |
| **Layer 2** (Learned) | Adapts | Specialist models with canary lifecycle (route classifier, anomaly detector) |
| **Layer 1** (Fallback) | Explains | LLM — only when solvers and specialists cannot handle the query |

Every action produces an auditable CaseTrajectory that feeds overnight learning.

## MAGMA Memory Architecture

| Layer | Component | Role |
|-------|-----------|------|
| L1 | AuditLog | Append-only event log — goals, plans, actions, verifications |
| L2 | ReplayEngine | Mission-level replay with chronological step reconstruction |
| L3 | MemoryOverlay | Filtered views by profile, mission, entity |
| L4 | Provenance | 9-tier source tracking: verifier, observed, solver, rule, stats, case, reflection, LLM, simulated |
| L5 | TrustEngine | Multi-dimensional scoring for agents, capabilities, solvers, routes, specialists |

## Security

- **No eval()**: All expression evaluation uses AST-based whitelist (`core/safe_eval.py`)
- **MQTT TLS**: Enabled by default (port 8883)
- **CI security scan**: GitHub Actions checks for eval() regression on every push
- **Safe Action Bus**: All write operations go through policy -> risk -> approval chain
- **ResourceGuard**: OOM protection with throttling and emergency GC

## Testing

```bash
pytest tests/ -q                    # 4350+ tests
pytest tests/ -k "safe_eval" -v     # Security tests specifically
python tools/generate_state.py      # Regenerate CURRENT_STATE.md
```

## Project State

See [`CURRENT_STATE.md`](CURRENT_STATE.md) for machine-readable module inventory with line counts, auto-generated from code.

Regenerate: `python tools/generate_state.py`

## Licensing

**Dual-licensed:**

- **Apache 2.0**: Infrastructure, tests, API endpoints, adapters
- **BUSL 1.1**: Protected files (dream mode, consolidator, meta-optimizer, projections)
  - Change Date: 2030-03-18 -> becomes Apache 2.0
  - See [`LICENSE-CORE.md`](LICENSE-CORE.md) for full list

**Commercial licensing contact:** janikorpi@hotmail.com

## Credits

Built by Jani Korpi (Helsinki) with Claude Code and other AI agents.

---

*WaggleDance — Local. Auditable. Autonomous.*

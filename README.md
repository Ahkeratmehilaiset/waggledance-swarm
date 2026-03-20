# WaggleDance

**Local-first, solver-first autonomy runtime** for sensor-rich and domain-specific environments.

WaggleDance routes every task to the optimal reasoning layer — solvers, specialist models, world model, rules, memory, or LLM — with full audit trail, autonomous overnight learning, and hardware-aware scaling from ESP32 to DGX.

No cloud. No API keys. No subscription.

---

## Why WaggleDance?

- **Solver-first intelligence** — physics, symbolic, constraint and optimization engines run before LLM. Math queries resolve in <2ms with verified correctness.
- **MAGMA audit backplane** — append-only provenance, causal replay, branchable overlays and multi-dimensional trust scoring across 5 layers.
- **Night Learning v2** — CaseTrajectory-based learning with quality gate (gold/silver/bronze/quarantine), specialist model training with canary promotion, and procedural memory.
- **Safe Action Bus** — deny-by-default policy engine with risk scoring, verifier, and rollback for every write operation.
- **Domain-agnostic core** — add new domains with YAML configs and Python adapters. Multiple profiles included out of the box.
- **Hardware-aware scaling** — automatic detection and tier selection from microcontrollers to GPU clusters. Same codebase everywhere.

---

## Architecture (3-Layer Design)

```
Query -> Intent Classification -> World Model Context
  -> Capability Selection (solver-first)
  -> Policy & Risk Evaluation
  -> Safe Action Bus (deny-by-default)
  -> Execution + Verification
  -> CaseTrajectory -> Night Learning v2
```

| Layer | Role | Examples |
|-------|------|----------|
| **Layer 3** (Authoritative) | Decides | Solvers, World Model, Policy Engine, Verifier |
| **Layer 2** (Learned) | Adapts | Specialist models with canary lifecycle (route classifier, anomaly detector) |
| **Layer 1** (Fallback) | Explains | LLM — only when solvers and specialists cannot handle the query |

Every action produces an auditable CaseTrajectory that feeds overnight learning.

---

## Deployment Profiles

| Profile | Target | Hardware | Typical Use Cases |
|---------|--------|----------|-------------------|
| **GADGET** | Edge / IoT | RPi, ESP32, Jetson Nano | Sensor calibration, battery optimization, environmental monitoring |
| **COTTAGE** | Off-grid property | Mini-PC, NUC | Heating control, frost protection, energy management, weather-aware scheduling |
| **HOME** | Smart home | Desktop, NAS, GPU workstation | Comfort automation, safety monitoring, energy optimization, multi-room orchestration |
| **FACTORY** | Industrial | Server, DGX, on-prem cluster | OEE, SPC, predictive maintenance. MQTT, ROS 2, OPC-UA, Modbus, Node-RED. Protocol-agnostic — Siemens, ABB, Mitsubishi, or any MQTT-capable system. Full MAGMA audit trail for ISO/GMP compliance. |

One-command deployment with hardware presets:

```bash
python start_waggledance.py --preset=raspberry-pi-iot   # GADGET
python start_waggledance.py --preset=cottage-full        # COTTAGE
python start_waggledance.py --preset=factory-production  # FACTORY
```

---

## Quick Start

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm

# Docker (recommended)
docker compose up -d

# Native (requires Ollama running locally)
pip install -r requirements.txt
python start_waggledance.py --preset=cottage-full
```

Dashboard: http://localhost:8000

---

## Current Status (v3.3 — March 2026)

- **4350 pytest tests**, CI green across Python 3.11/3.12/3.13
- Solver-first routing verified end-to-end (query -> solver -> verified answer -> gold case -> overnight learning)
- MAGMA audit trail captures full lifecycle (capability selection -> policy -> execution -> verification -> case recording)
- 8 specialist models with real sklearn training + canary promotion pipeline
- RAG-based fact verification, OpenTelemetry distributed tracing, ResourceGuard OOM protection
- CognitiveGraph: 5726 nodes, 6615 edges (agents + capabilities + intents)
- Hardware presets for one-command deployment (RPi IoT, cottage, factory)

---

## MAGMA Memory Architecture

| Layer | Component | Role |
|-------|-----------|------|
| L1 | AuditLog | Append-only event log — goals, plans, actions, verifications |
| L2 | ReplayEngine | Mission-level replay with chronological step reconstruction |
| L3 | MemoryOverlay | Filtered views by profile, mission, entity, canary vs production |
| L4 | Provenance | 9-tier source tracking: verifier, observed, solver, rule, stats, case, reflection, LLM, simulated |
| L5 | TrustEngine | Multi-dimensional scoring for agents, capabilities, solvers, routes, specialists |
| -- | CognitiveGraph | NetworkX knowledge graph with causal/semantic edges, JSON persistence |

---

## Roadmap

| Version | Focus |
|---------|-------|
| **v2.0** | Autonomy runtime, solver-first routing, MAGMA integration, night learning v2 |
| **v3.0** | Full alias migration, CognitiveGraph, domain-agnostic cutover |
| **v3.2** | Self-entity, epistemic uncertainty, dream mode, meta-optimizer, attention budget |
| **v3.3** (current) | Production hardening: god-object refactor, RAG verification, real LoRA V3, OTEL tracing, OOM protection, CI/CD, E2E tests |
| **Future** | Distributed multi-node clustering, federated learning, advanced LoRA specialist models |

---

## Project Structure

```
waggledance/
  core/
    autonomy/     # Runtime, lifecycle, resource kernel, metrics
    goals/        # Goal engine, mission store
    planning/     # Planner, verifier
    policy/       # Policy engine, risk scoring, constitution, approvals
    world/        # World model, baseline store, entity registry
    capabilities/ # Registry, selector, aliasing
    reasoning/    # Solver router, thermal/stats/causal/anomaly engines
    actions/      # Safe action bus, executor, rollback
    memory/       # Working memory, procedural memory
    learning/     # Night pipeline, case builder, quality gate, morning report
    magma/        # Provenance, replay, trust, audit adapters
    specialist_models/  # Trainer, model store
  application/services/ # Chat, learning, autonomy, world services
  adapters/             # LLM, HTTP, persistence, capability adapters
  bootstrap/            # Container, runtime builder
```

---

## Testing

```bash
python -m pytest tests/ -q          # Full suite (4350 tests)
python validate_cutover.py          # Autonomy cutover check
```

---

## License

WaggleDance is **dual-licensed**.

### Open core
Most of the repository is licensed under **Apache License 2.0**.
This includes the general runtime, adapters, policy scaffolding, CLI, tests, and other open-core components.

### Protected modules
Selected moat modules are licensed under **Business Source License 1.1 (BUSL-1.1)**.
These include the protected MAGMA audit components, CaseTrajectory quality-gate components, and the v3.2 protected modules listed in `LICENSE-CORE.md`.

### What you may do
- **Apache-2.0 files**: use them under normal Apache terms.
- **BUSL-1.1 files**: use them freely for development, testing, evaluation, CI, education, security review, and research.
- **BUSL-1.1 files** may also be used in production for **personal, non-commercial use**.

### Commercial production use
If you want to use the protected BUSL-1.1 modules in a commercial product, commercial service, managed service, hosted offering, or competitive offering, you need a separate commercial license.

**Commercial licensing contact:** janikorpi@hotmail.com

If you are unsure whether your use counts as commercial production use, ask before shipping.

### Change date
BUSL-1.1 protected files convert to **Apache License 2.0 on 2030-03-18**.

See `LICENSE`, `LICENSE-BUSL.txt`, and `LICENSE-CORE.md` for details.

---

## Credits

Built by Jani Korpi (Helsinki) with Claude Code and other AI agents.

---

*WaggleDance — Local. Auditable. Autonomous.*

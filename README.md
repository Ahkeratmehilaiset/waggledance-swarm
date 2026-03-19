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
- **Domain-agnostic core** — add new domains with YAML configs and Python adapters. Beekeeping, smart home, and industrial profiles are included examples.
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

| Profile | Target | Typical Use Cases |
|---------|--------|-------------------|
| **GADGET** | Edge / IoT | Battery optimization, sensor calibration |
| **COTTAGE** | Off-grid property | Heating, frost protection, energy management |
| **HOME** | Smart home | Comfort, safety, energy optimization |
| **FACTORY** | Industrial lines | OEE, SPC, predictive maintenance. Integrates via MQTT with ROS 2, OPC-UA, Modbus, Node-RED. Protocol-agnostic — works with Siemens, ABB, Tesla, BYD, Mitsubishi, or any MQTT-capable system. Full MAGMA audit trail for ISO/GMP compliance. |
| **APIARY** | Beekeeping | Colony health, varroa detection, seasonal tasks |

---

## Quick Start

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm

# Docker (recommended)
docker compose up -d

# Native (requires Ollama running locally)
pip install -r requirements.txt
python -m waggledance.adapters.cli.start_runtime --profile HOME
```

Dashboard: http://localhost:8000

---

## Current Status (March 2026)

- **v3.2** — full autonomy runtime with self-entity, epistemic uncertainty, dream mode
- **4074 pytest tests passing**, CI green
- Solver-first routing verified end-to-end (query -> solver -> verified answer -> gold case -> overnight learning)
- MAGMA audit trail captures full lifecycle (capability selection -> policy -> execution -> verification -> case recording)
- Specialist model training functional with sklearn route classifier; canary promotion pipeline in place
- v3.2 modules: epistemic uncertainty, attention budget, dream mode, consolidator, meta-optimizer, projections
- CognitiveGraph populated: 5726 nodes, 6615 edges (agents + capabilities + intents)

**Known limitations:**
- Specialist model training beyond route classifier uses simulated accuracy
- Dashboard HTML has not been fully domain-neutralized
- Legacy entrypoints (main.py, start.py) still exist alongside new runtime

---

## MAGMA Memory Architecture

| Layer | Component | Role |
|-------|-----------|------|
| L1 | AuditLog | Append-only event log — goals, plans, actions, verifications |
| L2 | ReplayEngine | Mission-level replay with chronological step reconstruction |
| L3 | MemoryOverlay | Filtered views by profile, mission, entity, canary vs production |
| L4 | Provenance | Tiered source tracking: observed, solver, stats, rule, LLM, verifier |
| L5 | TrustEngine | Multi-dimensional scoring for agents, capabilities, solvers, routes |
| -- | CognitiveGraph | NetworkX knowledge graph with causal/semantic edges, JSON persistence |

---

## Roadmap

| Version | Focus |
|---------|-------|
| **v2.0.0** | Autonomy runtime, solver-first routing, MAGMA integration, night learning v2 |
| **v3.0** | Full alias migration, CognitiveGraph auto-population, complete domain-agnostic cutover |
| **v3.2** (current) | Self-entity in World Model, epistemic uncertainty, dream mode, meta-optimizer, attention budget, projections |
| **Future** | Distributed multi-node clustering, advanced LoRA specialist models |

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
python -m pytest tests/ -q          # Full suite
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

Built by Jani Korpi (Ahkerat Mehilaiset, Helsinki) with Claude Code.

Originally developed for Finnish commercial beekeeping (300 hives, 10,000 kg honey/year). The architecture proved general enough to become a domain-agnostic autonomy runtime.

---

*WaggleDance — Local. Auditable. Autonomous.*

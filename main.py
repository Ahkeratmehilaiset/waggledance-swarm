#!/usr/bin/env python3
"""
WaggleDance Swarm AI — Local HiveNode Runtime (On-Prem)
========================================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v1.1.0 • Built: 2026-03-13

v1.1.0: FAISS vector store, ReasoningDashboard, Classic/Reasoning toggle, /api/solve endpoint.
v1.0.0: Production hardening, V1 API wiring, agent rotation fix, MAGMA deep wiring, live optimization.
v0.9.0: hivemind.py refactor (3321→1382 lines), Phi-3.5-mini LoRA, Sonnet review fixes (C2-C3, H2-H6, M2-M7).
"""
import asyncio
import sys
import os
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# KRIITTINEN: Windows UTF-8 -korjaus
# Ilman tätä: Päämehiläishoitaja → PÃ¤Ã¤mehilÃ¤ishoitaja
# Kolme kerrosta:
#   1. chcp 65001 → Windows-konsolin koodisivu UTF-8:ksi
#   2. PYTHONUTF8=1 → Python käyttää UTF-8:aa kaikkialla
#   3. reconfigure → stdout/stderr UTF-8 (varmuudeksi)
# ═══════════════════════════════════════════════════════════════
if sys.platform == "win32":
    # Kerros 1: Windows-konsolin koodisivu
    os.system("chcp 65001 > nul 2>&1")

    # Kerros 2: Python UTF-8 Mode (PEP 540, Python 3.7+)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # Kerros 2b: Unbuffered stdout — prints näkyvät heti Windowsissa
    os.environ["PYTHONUNBUFFERED"] = "1"

    # Kerros 3: Rekonfiguroi stdout/stderr
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# PHASE1 TASK7: Ollama model keep-alive and max loaded models
os.environ.setdefault("OLLAMA_KEEP_ALIVE", "24h")
os.environ.setdefault("OLLAMA_MAX_LOADED_MODELS", "4")

# ── Auto-install dependencies ──
if "--no-auto-install" not in sys.argv:
    sys.path.insert(0, str(Path(__file__).parent))
    from core.auto_install import ensure_dependencies
    ensure_dependencies()
    # Remove flag so argparse (if any) doesn't complain
    if "--no-auto-install" in sys.argv:
        sys.argv.remove("--no-auto-install")
else:
    sys.argv.remove("--no-auto-install")
    sys.path.insert(0, str(Path(__file__).parent))

from hivemind import HiveMind

AUTO_SPAWN = [
    # Core business
    "beekeeper", "core_dispatcher", "flight_weather",
    # Bee monitoring (high priority)
    "disease_monitor", "swarm_watcher", "hive_temperature", "nectar_scout",
    # Environment
    "meteorologist", "horticulturist", "ornithologist",
    # Security
    "hive_security", "cyber_guard", "yard_guard",
    # Property
    "electrician", "chimney_sweep", "nature_photographer",
    # Forest & wildlife
    "forester", "wildlife_ranger",
    # Daily life
    "wilderness_chef", "sauna_master",
    # Special
    "oracle", "hacker",
]


async def main() -> None:
    """Initialize HiveMind, spawn agents, populate caches, and start the web server."""
    hive = HiveMind("configs/settings.yaml")
    await hive.start()

    # Filter AUTO_SPAWN by active profile (only spawn agents in templates)
    available_templates = set(hive.spawner.agent_templates.keys())
    active_profile = hive.config.get("profile", "cottage")
    spawn_list = [a for a in AUTO_SPAWN if a in available_templates]
    skipped = [a for a in AUTO_SPAWN if a not in available_templates]
    if skipped:
        print(f"  ℹ️  Profile '{active_profile}': skipped {len(skipped)} agents "
              f"not in profile ({', '.join(skipped[:5])}{'...' if len(skipped) > 5 else ''})")

    spawned = 0
    for agent_type in spawn_list:
        try:
            existing = hive.spawner.get_agents_by_type(agent_type)
            if not existing:
                agent = await hive.spawner.spawn(agent_type)
                spawned += 1
                # FIX-4: Auto-register scheduleriin hook-metodilla
                if agent:
                    hive.register_agent_to_scheduler(agent)
        except Exception as e:
            print(f"  ⚠️  {agent_type}: {e}")

    # YAML auto_spawn agentit
    for t, tmpl in hive.spawner.agent_templates.items():
        if tmpl.get("auto_spawn") and t not in AUTO_SPAWN:
            try:
                existing = hive.spawner.get_agents_by_type(t)
                if not existing:
                    agent = await hive.spawner.spawn(t)
                    spawned += 1
                    if agent:
                        hive.register_agent_to_scheduler(agent)
            except Exception:
                pass
            if spawned >= 25:
                break

    # Bulk-register: kaikki agentit YAMLBridgestä scheduleriin
    # Tämä rekisteröi myös ne agentit joita ei ole vielä spawnattu
    # mutta jotka löytyvät YAML-tiedostoista (50 agenttia).
    # Scheduler oppii niiden tags/skills valmiiksi.
    if hive._swarm_enabled and hive.scheduler and hasattr(hive.spawner, 'yaml_bridge'):
        bulk_count = hive.scheduler.register_from_yaml_bridge(
            hive.spawner.yaml_bridge,
            hive.spawner
        )
        print(f"  ✅ Swarm Scheduler: {bulk_count} agenttia rekisteröity YAMLBridgestä", flush=True)

    total = len(hive.spawner.active_agents)
    hb_model = (hive.llm_heartbeat.model
                if hasattr(hive, "llm_heartbeat") else hive.llm.model)
    hb_device = "CPU" if (hasattr(hive, "llm_heartbeat")
                          and hive.llm_heartbeat.num_gpu == 0) else "GPU"

    # ── Hardware + Throttle yhteenveto ──────────────────────
    _ts = hive.throttle.state if hive.throttle else None
    import psutil
    _cpu_count = psutil.cpu_count(logical=True)
    _ram_gb = psutil.virtual_memory().total / (1024**3)

    # GPU info
    _gpu_name = "?"
    _gpu_vram = "?"
    try:
        import subprocess
        _nv = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if _nv.returncode == 0:
            parts = _nv.stdout.strip().split(", ")
            _gpu_name = parts[0] if len(parts) > 0 else "?"
            _gpu_vram = f"{int(parts[1])} MiB" if len(parts) > 1 else "?"
    except Exception:
        pass

    _mc = _ts.machine_class.upper() if _ts else "?"
    _hbi = f"{_ts.heartbeat_interval:.0f}s" if _ts else "?"
    _conc = _ts.max_concurrent if _ts else "?"
    _idle = _ts.idle_every_n_heartbeat if _ts else "?"
    _batch = _ts.idle_batch_size if _ts else "?"

    print(f"""
  ╔══════════════════════════════════════════════════════╗
  ║  🐝 WaggleDance Swarm AI                            ║
  ╠══════════════════════════════════════════════════════╣
  ║  Hardware:                                           ║
  ║    GPU:  {_gpu_name:<20} {_gpu_vram:>10}        ║
  ║    CPU:  {_cpu_count} cores                {_ram_gb:>6.1f} GB RAM       ║
  ║    Class: {_mc:<12}  (auto-detected)           ║
  ╠══════════════════════════════════════════════════════╣
  ║  Models:                                             ║
  ║    Chat:      {hive.llm.model:<16} (GPU)               ║
  ║    Heartbeat: {hb_model:<16} ({hb_device})               ║
  ╠══════════════════════════════════════════════════════╣
  ║  Adaptive Scaling:                                   ║
  ║    Heartbeat interval: {_hbi:>6}                        ║
  ║    Max concurrent:     {_conc:>6}                        ║
  ║    Idle research:      every {_idle} HB, batch {_batch}          ║
  ║    Agents spawned:     {total:>6}                        ║
  ╠══════════════════════════════════════════════════════╣
  ║  Dashboard: http://localhost:8000                     ║
  ║  Stop:      Ctrl+C                                   ║
  ╚══════════════════════════════════════════════════════╝""", flush=True)

    if hive.scheduler:
        stats = hive.scheduler.get_stats()
        print(f"  Swarm: {stats['total_agents']} agents in scheduler "
              f"({'ENABLED' if hive._swarm_enabled else 'DISABLED'})", flush=True)

    # ── A1: Populoi nopeat muistivarastot olemassa olevalla datalla ──
    if hive.consciousness:
        _c = hive.consciousness
        _pop_total = 0
        # Bilingual FI index (nomic-embed, ~55ms search)
        if _c.bilingual and _c.bilingual.fi_count == 0 and _c.memory.count > 0:
            print("  ⏳ Populoidaan bilingual FI-index...", flush=True)
            _n = _c.bilingual.populate_from_memory()
            _pop_total += _n
            print(f"  ✅ Bilingual FI: {_n} faktaa ladattu", flush=True)
        # fi_fast store (all-minilm, ~18ms search)
        if _c.fi_fast and _c.fi_fast.fi_fast_count == 0 and _c.memory.count > 0:
            print("  ⏳ Populoidaan fi_fast-varasto...", flush=True)
            _n = _c.fi_fast.populate_from_memory(_c.memory)
            _pop_total += _n
            print(f"  ✅ fi_fast: {_n} faktaa ladattu", flush=True)
        if _pop_total > 0:
            print(f"  🚀 Nopeat polut aktivoitu: {_pop_total} faktaa "
                  f"(HotCache=0→täyttyy kyselyistä)", flush=True)
        elif _c.memory.count > 0:
            _fi_n = _c.bilingual.fi_count if _c.bilingual else 0
            _ff_n = _c.fi_fast.fi_fast_count if _c.fi_fast else 0
            print(f"  ✅ Nopeat polut: bilingual={_fi_n}, fi_fast={_ff_n}, "
                  f"hot_cache={_c.hot_cache.size if _c.hot_cache else 0}", flush=True)

    try:
        from web.dashboard import create_app
        app = create_app(hive)
        import uvicorn
        config = uvicorn.Config(
            app, host="0.0.0.0", port=8000, log_level="warning"
        )
        server = uvicorn.Server(config)
        await server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        await hive.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 WaggleDance sammutettu.")
